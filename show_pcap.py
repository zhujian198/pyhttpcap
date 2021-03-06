#!/usr/bin/env python
#coding=utf-8
from Queue import Queue
import StringIO
import argparse
import io
import packet_parser
import textutils

__author__ = 'dongliu'

import sys
from collections import OrderedDict
import struct

import pcap
import pcapng
from httpparser import HttpType, HttpParser
from config import parse_config


class HttpConn:
    """all data having same source/dest ip/port in one http connection."""
    STATUS_BEGIN = 0
    STATUS_RUNNING = 1
    STATUS_CLOSED = 2
    STATUS_ERROR = -1

    def __init__(self, tcp_pac, outputfile):
        self.source_ip = tcp_pac.source
        self.source_port = tcp_pac.source_port
        self.dest_ip = tcp_pac.dest
        self.dest_port = tcp_pac.dest_port

        self.status = HttpConn.STATUS_BEGIN
        self.out = outputfile

        # start parser thread
        self.http_parser = HttpParser((self.source_ip, self.source_port),
                                      (self.dest_ip, self.dest_port), parse_config)
        self.append(tcp_pac)

    def append(self, tcp_pac):
        if len(tcp_pac.body) == 0:
            return
        if self.status == HttpConn.STATUS_ERROR or self.status == HttpConn.STATUS_CLOSED:
            # not http conn or conn already closed.
            return

        if self.status == HttpConn.STATUS_BEGIN:
            if tcp_pac.body:
                if textutils.ishttprequest(tcp_pac.body):
                    self.status = HttpConn.STATUS_RUNNING
        if tcp_pac.pac_type == -1:
            # end of connection
            if self.status == HttpConn.STATUS_RUNNING:
                self.status = HttpConn.STATUS_CLOSED
            else:
                self.status = HttpConn.STATUS_ERROR

        if tcp_pac.source == self.source_ip:
            httptype = HttpType.REQUEST
        else:
            httptype = HttpType.RESPONSE

        if tcp_pac.body:
            self.http_parser.send((httptype, tcp_pac.body))

    def finish(self):
        result = self.http_parser.finish()
        self.out.write(result)
        self.out.flush()


class FileFormat(object):
    PCAP = 0xA1B2C3D4
    PCAP_NG = 0x0A0D0D0A
    UNKNOW = -1


def get_file_format(infile):
    """get cap file format by magic num"""
    buf = infile.read(4)
    infile.seek(0)
    magic_num, = struct.unpack('<I', buf)
    if magic_num == 0xA1B2C3D4 or magic_num == 0x4D3C2B1A:
        return FileFormat.PCAP
    elif magic_num == 0x0A0D0D0A:
        return FileFormat.PCAP_NG
    else:
        return FileFormat.UNKNOW


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("infile", help="the pcap file to parse")
    parser.add_argument("-i", "--ip", help="only parse packages with specified source OR dest ip")
    parser.add_argument("-p", "--port", type=int, help="only parse packages with specified source OR dest port")
    parser.add_argument("-v", "--verbosity", help="increase output verbosity(-vv is recommended)", action="count")
    parser.add_argument("-o", "--output", help="output to file instead of stdout")
    parser.add_argument("-e", "--encoding", help="decode the data use specified encodings.")
    parser.add_argument("-b", "--beauty", help="output json in a pretty way.", action="store_true")

    args = parser.parse_args()

    filepath = args.infile
    port = args.port
    ip = args.ip

    if args.verbosity:
        parse_config.level = args.verbosity
    if args.encoding:
        parse_config.encoding = args.encoding
    parse_config.pretty = args.beauty

    if args.output:
        outputfile = open(args.output, "w+")
    else:
        outputfile = sys.stdout

    try:
        with io.open(filepath, "rb") as infile:
            file_format = get_file_format(infile)
            if file_format == FileFormat.PCAP:
                pcap_file = pcap.PcapFile(infile).read_packet
            elif file_format == FileFormat.PCAP_NG:
                pcap_file = pcapng.PcapNgFile(infile).read_packet
            else:
                print >> sys.stderr, "unknow file format."
                sys.exit(1)

            conn_dict = OrderedDict()
            for tcp_pac in packet_parser.read_package_r(pcap_file):
                #filter
                if port is not None and tcp_pac.source_port != port and tcp_pac.dest_port != port:
                    continue
                if ip is not None and tcp_pac.source != ip and tcp_pac.dest != ip:
                    continue

                key = tcp_pac.gen_key()
                # we already have this conn
                if key in conn_dict:
                    conn_dict[key].append(tcp_pac)
                    # conn closed.
                    if tcp_pac.pac_type == packet_parser.TcpPack.TYPE_CLOSE:
                        conn_dict[key].finish()
                        del conn_dict[key]

                # begin tcp connection.
                elif tcp_pac.pac_type == 1:
                    conn_dict[key] = HttpConn(tcp_pac, outputfile)
                elif tcp_pac.pac_type == 0:
                    # tcp init before capature, we found a http request header, begin parse
                    # if is a http request?
                    if textutils.ishttprequest(tcp_pac.body):
                        conn_dict[key] = HttpConn(tcp_pac, outputfile)

            for conn in conn_dict.values():
                conn.finish()
    finally:
        if args.output:
            outputfile.close()
        sys.exit()


if __name__ == "__main__":
    main()