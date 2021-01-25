#!/usr/bin/env python3
#
# Copyright (c) 2021 Seagate Technology LLC and/or its Affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For any questions about this software or licensing,
# please email opensource@seagate.com or cortx-questions@seagate.com.
#
import sys
import errno
import os
import re
import subprocess
import time
from cortx.utils.conf_store import Conf

MOTR_KERNEL_FILE = "/lib/modules/{kernel_ver}/kernel/fs/motr/m0tr.ko"
MOTR_SYS_FILE = "/etc/sysconfig/motr"
MOTR_CONFIG_SCRIPT = "/opt/seagate/cortx/motr/libexec/motr_cfg.sh"
LNET_CONF_FILE = "/etc/modprobe.d/lnet.conf"
SYS_CLASS_NET_DIR = "/sys/class/net/"
SLEEP_SECS = 2
TIMEOUT_SECS = 120

class MotrError(Exception):
    """ Generic Exception with error code and output """

    def __init__(self, rc, message, *args):
        self._rc = rc
        self._desc = message % (args)
        sys.stderr.write("error(%d): %s\n" %(self._rc, self._desc))

    def __str__(self):
        if self._rc == 0: return self._desc
        return "error(%d): %s" %(self._rc, self._desc)

def write_sep_line():
    for i in range(80):
        sys.stdout.write("=")
    sys.stdout.write("\n")

def execute_command(cmd, timeout_secs):

    ps = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          shell=True)
    stdout, stderr = ps.communicate(timeout=timeout_secs);
    stdout = str(stdout, 'utf-8')
    write_sep_line()
    sys.stdout.write(f"[CMD] {cmd}\n")
    sys.stdout.write(f"[OUT]\n{stdout}\n")
    sys.stdout.write(f"[RET] {ps.returncode}\n")
    write_sep_line()
    return stdout, ps.returncode

def start_services(services):
    for service in services:
        cmd = "service {} start".format(service)
        execute_command(cmd, TIMEOUT_SECS)
        cmd = "service {} status".format(service)
        execute_command(cmd, TIMEOUT_SECS)

def validate_file(file):
    if not os.path.exists(file):
        raise MotrError(errno.ENOENT, "{} not exist".format(file))

def is_hw_node():
    cmd = "systemd-detect-virt"
    op  = execute_command(cmd, TIMEOUT_SECS)
    op  = op[0].split('\n')[0]
    if op == "none":
        return True
    else:
        return False

def validate_motr_rpm(self):
    try:
        cmd = "uname -r"
        cmd_res = execute_command(cmd, TIMEOUT_SECS)
        op = cmd_res[0]
        kernel_ver = op.replace('\n', '')
        kernel_module = f"/lib/modules/{kernel_ver}/kernel/fs/motr/m0tr.ko"
        sys.stdout.write(f"[INFO] Checking for {kernel_module}\n")
        validate_file(kernel_module)
        sys.stdout.write(f"[INFO] Checking for {MOTR_SYS_FILE}\n")
        validate_file(MOTR_SYS_FILE)
    except MotrError as e:
        pass

def motr_config(self):
    is_hw = is_hw_node()
    if is_hw:
        execute_command(MOTR_CONFIG_SCRIPT, TIMEOUT_SECS)

def configure_net(self):
     '''Wrapper function to detect lnet/libfabric transport'''
     configure_lnet_from_conf_store(self)

def configure_lnet_from_conf_store(self):
    '''
       Get iface and /etc/modprobe.d/lnet.conf params from
       conf store. Configure lnet. Start lnet service
    '''
    iface = Conf.get(self._index,
         f'cluster>server')[self._server_id]['network']['data']['interfaces'][1]
    hw_node = is_hw_node()
    if hw_node:
        iface_type = "o2ib"
    else:
        iface_type = "tcp"
    sys.stdout.write(f"[INFO] {iface_type}=({iface})")
    sys.stdout.write(f"[INFO] Updating {LNET_CONF_FILE}")
    with open(LNET_CONF_FILE, "w") as fp:
        fp.write(f"options lnet networks={iface_type}({iface}) "
                 f"config_on_load=1  lnet_peer_discovery_disabled=1\n")
        time.sleep(SLEEP_SECS)
        start_services(["lnet"])


def create_lvm(node_name, metadata_dev):
    try:
        validate_file(metadata_dev)

        cmd = f"fdisk -l {metadata_dev}"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = "swapoff -a"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = f"pvcreate {metadata_dev}"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = f"vgcreate  vg_metadata_{node_name} {metadata_dev}"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = f"vgchange --addtag {node_name} vg_metadata_{node_name}"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = "vgscan --cache"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = f"lvcreate -n lv_main_swap vg_metadata_{node_name} -l 51%VG"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = f"lvcreate -n lv_raw_metadata vg_metadata_{node_name} -l 100%FREE"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = f"mkswap -f /dev/vg_metadata_{node_name}/lv_main_swap"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = f"test -e /dev/vg_metadata_{node_name}/lv_main_swap"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = f"swapon /dev/vg_metadata_{node_name}/lv_main_swap"
        execute_command(cmd, TIMEOUT_SECS)

        cmd = (
           f"echo \"/dev/vg_metadata_{node_name}/lv_main_swap    swap    "
           f"swap    defaults        0 0\" >> /etc/fstab"
        )
        execute_command(cmd, TIMEOUT_SECS)
    except:
        pass

def config_lvm(self):
    node_name = Conf.get(self._index,
              f'cluster>server')[self._server_id]['hostname']
    node_name = node_name.split('.')[0]
    metadata_device = Conf.get(self._index,
              f'cluster>server')[self._server_id]['storage']['metadata_devices']
    sys.stdout.write(f"[INFO] server_id={self._server_id} node_name={node_name}"
                     f" metadata_device={metadata_device[0]}\n")
    create_lvm(node_name, metadata_device[0])

def get_lnet_xface() -> str:
    lnet_xface = None
    try:
        with open(LNET_CONF_FILE, 'r') as f:
            # Obtain interface name
            for line in f.readlines():
                if len(line.strip()) <= 0: continue
                tokens = re.split(r'\W+', line)
                if len(tokens) > 4:
                    lnet_xface = tokens[4]
                    break
    except:
        pass

    if lnet_xface == None:
        raise MotrError(errno.EINVAL, "Cant obtain iface details from %s"
                        , LNET_CONF_FILE)
    if lnet_xface not in os.listdir(SYS_CLASS_NET_DIR):
        raise MotrError(errno.EINVAL, "Invalid iface %s in lnet.conf"
                        , lnet_xface)

    return lnet_xface

def check_pkgs(src_pkgs, dest_pkgs):
    missing_pkgs = []
    for src_pkg in src_pkgs:
        found = False
        for dest_pkg in dest_pkgs:
            if src_pkg in dest_pkg:
                found = True
                break
        if found == False:
            missing_pkgs.append(src_pkg)
    if missing_pkgs:
        raise MotrError(errno.ENOENT, f'Missing pkgs: {missing_pkgs}')

def ping_other_nodes(self):
    try: 
        cmd = "hostname"
        my_hostname, ret_code = execute_command(cmd, TIMEOUT_SECS)
        my_hostname = my_hostname.rstrip("\n")
        if(ret_code):
            raise MotrError(ret_code, "Failed cmd={cmd} ret={ret_code}") 
        servers_data = (Conf.get(self._index,
                                 f'cluster>server'))
        for server_item in servers_data:
            if (my_hostname != server_item["hostname"]):
                temp_hostname = server_item["hostname"]
                ifaces = server_item["network"]["data"]["interfaces"]
                for iface in ifaces:
                    cmd = f"ping -c 3 -I {iface} {temp_hostname}"
                    op, ret_code = execute_command(cmd, TIMEOUT_SECS)
                    if (ret_code != 0):
                        sys.stderr.write(f"Error: ping failed on {temp_hostname}:{iface}\n")
                    time.sleep(SLEEP_SECS)
    except MotrError as e:
        pass  

def get_nids(nodes, myhostname):
    nids = []
    for node in nodes:
        if node == myhostname:
            cmd = f"lctl list_nids"
        else:
            cmd = f"ssh {node} lctl list_nids"
        op, ret = execute_command(cmd, TIMEOUT_SECS)
        nids.append(op.rstrip("\n"))
    return nids

def remove_lnet_selftest_module(nodes, myhostname):
    for node in nodes:
        if node == myhostname:
            cmd = f"lsmod | grep lnet_selftest"
        else:
            cmd = f"ssh {node} lsmod | grep lnet_selftest"
        op, ret_code = execute_command(cmd, TIMEOUT_SECS)

        # If module is present, remove it. Else, ignore.
        if ret_code == 0:
            if node == myhostname:
                cmd = f"rmmod lnet_selftest"
            else:
                cmd = f"ssh {node} rmmod lnet_selftest"
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)

def install_lnet_selftest_module(nodes, myhostname):
    for node in nodes:
        if node == myhostname:
           cmd = f"lsmod | grep lnet_selftest"
        else:
           cmd = f"ssh {node} lsmod | grep lnet_selftest"
        op, ret_code = execute_command(cmd, TIMEOUT_SECS)

        #If module is not present, install it. Else, ignore.
        if ret_code:
            if node == myhostname:
                cmd = "modprobe lnet_selftest"
            else:
                cmd = f"ssh {node} modprobe lnet_selftest"
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)
            if ret_code:
                raise MotrError(ret_code, "Failed cmd={cmd} ret={ret_code}")

'''
    First remove lent_selftest kernel modules from all nodes.
    Install lnet_selftest module in all nodes.
    Create pairs of nodes.
    For each pair, run rbw test
    Remove lnet_selftest module
'''
def lnet_selftest(self):
    try: 
        servers_data = (Conf.get(self._index,
                                 f'cluster>server'))
        nodes = []

        # Get all nodes from Conf
        for server_item in servers_data:
            nodes.append(server_item["hostname"])

        # Get my hostname
        cmd = "hostname"
        my_hostname, ret_code = execute_command(cmd, TIMEOUT_SECS)
        my_hostname = my_hostname.rstrip("\n")
        if(ret_code):
            raise MotrError(ret_code, "Failed cmd={cmd} ret={ret_code}") 

        # Get lnet ids of all nodes
        nids = get_nids(nodes, my_hostname)
        install_lnet_selftest_module(nodes, my_hostname)

        # Create nid pairs
        total_nids = len(nids)
        nid_pairs = []
        for i in range(total_nids):
           for j in range(i+1, total_nids):
                nid_pairs.append([nids[i], nids[j]])

        # For each client-server pair, perform selftest 
        for nid_pair in nid_pairs:
            pid = os.getpid()
            os.environ['LST_SESSION'] = f"{pid}"
            cmd = "lst new_session twonoderead"
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)
            if(ret_code):
                continue

            cmd = f"lst add_group client {nid_pair[0]}"
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)
            if(ret_code):
                execute_command("lst end_session", TIMEOUT_SECS)
                continue

            cmd = f"lst add_group server {nid_pair[1]}"
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)
            if(ret_code):
                execute_command("lst end_session", TIMEOUT_SECS)
                continue

            cmd = f"lst add_batch bulk_read"
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)
            if(ret_code):
                execute_command("lst end_session", TIMEOUT_SECS)
                continue

            cmd = (f"lst add_test --batch bulk_read "
                    "--from client --to server brw read check=full size=1M")
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)
            if(ret_code):
                execute_command("lst end_session", TIMEOUT_SECS)
                continue

            sys.stdout.write(f"Running brw read between {nid_pair[0]} "
                             f"and {nid_pair[1]} for 30s\n")
            cmd = "lst run bulk_read"
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)
            if(ret_code):
                execute_command("lst end_session", TIMEOUT_SECS)
                continue

            cmd = "timeout -k 35 30 lst stat client server"
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)

            cmd = "lst stop bulk_read"
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)
            if(ret_code):
                execute_command("lst end_session", TIMEOUT_SECS)
                continue

            cmd = "lst end_session"
            op, ret_code = execute_command(cmd, TIMEOUT_SECS)
            if(ret_code):
                remove_lnet_selftest_module(nodes, my_hostname)
                raise MotrError(ret_code, "Failed cmd={cmd} ret={ret_code}")
            time.sleep(SLEEP_SECS)
        remove_lnet_selftest_module(nodes, my_hostname)
    except MotrError as e:
        pass

def test_lnet(self):
    search_lnet_pkgs = ["kmod-lustre-client", "lustre-client"]

    try:
        # Check missing luster packages
        cmd = f'rpm -qa | grep lustre'
        cmd_res = execute_command(cmd, TIMEOUT_SECS)
        temp = cmd_res[0]
        lustre_pkgs = list(filter(None, temp.split("\n")))
        check_pkgs(search_lnet_pkgs, lustre_pkgs)

        lnet_xface = get_lnet_xface()
        ip_addr = os.popen(f'ip addr show {lnet_xface}').read()
        ip_addr = ip_addr.split("inet ")[1].split("/")[0]
        cmd = "ping -c 3 {}".format(ip_addr)
        cmd_res = execute_command(cmd, TIMEOUT_SECS)
        sys.stdout.write("{}\n".format(cmd_res[0]))
        ping_other_nodes(self)
        time.sleep(SLEEP_SECS)
        lnet_selftest(self)
    except MotrError as e:
        pass