#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#    Copyright (C) 2017 Tyler Cipriani
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software Foundation,
#    Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
#
# sshecret is a wrapper around ssh that automatically manages multiple
# ssh-agent(1)s each containing only a single ssh key.

import argparse
import errno
import hashlib
import logging
import os
from shlex import quote
import subprocess
import sys


try:
    from paramiko import SSHConfig
except ImportError:
    print("[ERROR] sudo apt-get install python-paramiko")
    sys.exit(1)


DESCRIPTION = '''
sshecret is a wrapper around ssh that automatically manages multiple
ssh-agent(1)s each containing only a single ssh key.

    EXAMPLE: sshecret -A -L8080:localhost:80 -l johndoe -p2222 example.com

sshecret accepts the same parameters as ssh(1) - fundamentally sshecret uses
execve(2) to wrap ssh, modifying the environment to ensure that each key in
your ssh_config(5) uses its own ssh-agent.

In order to retrieve the path to the socket for a given hostname, use:

    sshecret --socket hostname
'''


LOG_FORMAT = '%(asctime)s %(filename)s %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


# Intentionally omit ``-v`` since I want to be able to see debug output
# for this script when passing ``-v`` as well.
SSH_FLAGS = ['-1', '-2', '-4', '-6', '-A', '-a', '-C', '-f', '-g',
             '-K', '-k', '-M', '-N', '-n', '-q', '-s', '-T', '-t',
             '-V', '-X', '-x', '-Y', '-y']


SSH_ARGS = ['-b', '-c', '-D', '-E', '-e', '-F', '-I', '-i', '-J', '-L',
            '-l', '-m', '-O', '-o', '-p', '-Q', '-R', '-S', '-w', '-W']


class SSHSock():
    """
    Creates an ssh-agent socket and adds the approriate runtime variables.
    """

    def __init__(self, key):
        self.key = key

    def get_sock_path(self):
        """
        Path for SSH socket files
        """
        checksum = hashlib.md5()
        checksum.update(self.key.file.encode("utf-8"))
        hexdigest = checksum.hexdigest()

        sock = os.path.join(
            os.getenv("XDG_RUNTIME_DIR"), "{}.sock".format(hexdigest))
        logging.debug("Sock path is: {}".format(sock))
        return sock

    def _add_key(self, sock_file):
        """
        Add key to existing socket
        """
        env = os.environ.copy()
        env['SSH_AUTH_SOCK'] = sock_file

        if self.key.check_key_exists(env):
            logging.debug(
                "SSH Key {} already in sock".format(self.key.file))
            return

        logging.debug(self.key.file)
        cmd = ["/usr/bin/ssh-add", self.key.file]
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                env=env)

        (stdout, stderr) = proc.communicate()

        if proc.returncode:
            raise OSError(
                1,
                "Could not add identityfile sock file",
                self.key.file
            )

    def create(self):
        """
        Checksums the identity file and creates a new socket.

        New socket will contain *only that one key*.
        """
        if self.key.empty:
            return

        sock_file = self.get_sock_path()
        if not os.path.exists(sock_file):
            cmd = [
                "/usr/bin/ssh-agent",
                "-a{}".format(sock_file),
            ]

            error = subprocess.check_call(cmd)
            if error:
                raise OSError(1, "Could not create sock file", sock_file)

        self._add_key(sock_file)
        return sock_file


class SSHKey():
    """
    Abstract the SSH identity file finding and checksumming.
    """

    def __init__(self, host):
        """
        Find the identity file based on hostname
        """
        config = SSHConfig()
        config.parse(open(self._get_ssh_config()))
        host_config = config.lookup(host)
        id_file = host_config.get("identityfile")
        self.id_file = None
        self.fingerprint = None
        if id_file is not None:
            self.id_file = id_file[::-1][0]
            logging.debug("SSH identity file is: {}".format(self.id_file))

    def _get_ssh_config(self):
        """
        Try to find ssh config file at default location
        """
        default = os.path.join(os.getenv("HOME"), ".ssh", "config")
        path = os.getenv("SSH_CONF_PATH", default)
        logging.debug("SSH config path is: {}".format(path))
        if not os.path.exists(path) or not os.path.isfile(path):
            raise IOError(
                errno.ENOENT,
                "File not found", path)

        return path

    def get_fingerprint(self):
        """
        Return fingerprint of SSH identity file
        """
        if self.id_file is None:
            return None

        if self.fingerprint is not None:
            return self.fingerprint

        cmd = [
            '/usr/bin/ssh-keygen',
            '-l',
            '-f',
            self.id_file]

        out = subprocess.check_output(cmd)
        self.fingerprint = out.strip().split()[1]
        logging.debug("Key fingerprint is: {}".format(self.fingerprint))
        return self.fingerprint

    def check_key_exists(self, env):
        cmd = ["ssh-add", "-l"]
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                env=env)
        out, err = proc.communicate()
        return self.get_fingerprint() in out.split()

    @property
    def empty(self):
        return self.id_file is None

    @property
    def file(self):
        return self.id_file


def parse_known_args(args):
    """Parse commandline arguments."""
    parser = argparse.ArgumentParser(
        usage='sshecret [--socket] [whatever you want to pass to ssh]',
        description=DESCRIPTION,
        formatter_class=argparse.RawTextHelpFormatter)
    for flagname in SSH_FLAGS:
        parser.add_argument(flagname, action='count',
                            help=argparse.SUPPRESS)
    for optionname in SSH_ARGS:
        parser.add_argument(optionname, help=argparse.SUPPRESS)

    parser.add_argument('-v', action='count', dest="verbose",
                        help='Increase verbosity of output')

    parser.add_argument('--socket', dest="sshecret_print_socket",
                        action='store_true',
                        help='print socket path for the given host')

    parser.add_argument('hostname', help=argparse.SUPPRESS)
    parser.add_argument('command', nargs='?', help=argparse.SUPPRESS)
    return parser.parse_known_args(args)


def setup_logging(verbose):
    """
    Setup logging level based on passed args.
    """
    level = logging.INFO

    if verbose > 0:
        level = logging.DEBUG

    logging.root.handlers = []
    logging.basicConfig(level=level, format=LOG_FORMAT)


def get_host(hostname):
    """Extract hostname from ssh-style command line args"""
    # Handle the [user]@[hostname] syntax
    if '@' in hostname:
        hostname = hostname.split('@')[1]

    # If for some whacky reason the hostname has a protocol...
    if hostname.startswith('ssh://'):
        hostname = hostname[len('ssh://'):]

    # Handle a port in the hostname
    if ':' in hostname:
        hostname = hostname.split(':')[0]

    logging.debug('Hostname is {}'.format(hostname))
    return hostname


def run_ssh(args, sock=None):
    """
    Exec ssh in the environemnt
    """
    env = {}
    if sock is not None:
        logging.info('SSH_AUTH_SOCK={}'.format(sock))
        env = os.environ.copy()
        env["SSH_AUTH_SOCK"] = sock

    ssh = ["/usr/bin/ssh"]
    ssh.extend(args)
    os.execve("/usr/bin/ssh", ssh, env)


def main(args):
    """
    Handle the whole deal.

    #. Find the host
    #. Find the keyfile for the host in the ssh config
    #. Create the ssh-agent and socket
    #. Exec ssh
    """
    args, extra = parse_known_args(args)

    verbose = 0
    if args.verbose:
        verbose = args.verbose
    setup_logging(verbose)

    host = get_host(args.hostname)
    sock = SSHSock(SSHKey(host))

    # If other sshecret-specific arguments are added which don't do an early
    # return before calling ssh(1), it may be necessary to filter sys.argv.
    if args.sshecret_print_socket:
        print('SSH_AUTH_SOCK={}'.format(quote(sock.get_sock_path())))
        return

    run_ssh(sys.argv[1:], sock.create())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
