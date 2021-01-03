#!/usr/bin/env python3
'''
Main CLI to herbstlufthub, use "hh" from the CLI.
'''
import sys
import zmq
import time
import click
import subprocess

default_addresses = ("ipc://hh.ipc",)
all_topics = ("",)

def make_socket(ztype, link, addresses=default_addresses,
                topics=all_topics):
    context = zmq.Context()
    socket = context.socket(ztype)    
    if ztype == zmq.SUB:
        for topic in topics:
            socket.setsockopt_string(zmq.SUBSCRIBE, topic)

    for addr in addresses:
        if link == "bind":
            socket.bind(addr)
        else:
            socket.connect(addr)
    return socket


@click.group()
@click.option("-l","--link",
              type=click.Choice(["bind","connect"]),
              default="connect",
              help="What end of the link")
@click.option("-a","--address", multiple=True,
              help="A ZeroMQ address string")
@click.option("-t","--topic", multiple=True,
              help="Prefix topic for subscriber")
@click.pass_context
def cli(ctx, link, address, topic):
    '''
    herbstlufthub MPMC pub/sub
    '''
    ctx.obj = dict(
        link = link,
        topics = topic or all_topics,
        addresses = address or default_addresses)


@cli.command("hcpub")
@click.pass_context
def hcpub(ctx):
    '''
    A PUB of events from a herbstclient, binds.
    '''
    socket = make_socket(zmq.PUB, **ctx.obj)
    proc = subprocess.Popen("/usr/bin/herbstclient --idle",
                            shell=True,
                            universal_newlines=True,
                            text=True, bufsize=1,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    while True:
        line = proc.stdout.readline()
        sys.stderr.write(line)
        line = line.strip()
        socket.send_string(line)

@cli.command("stdpub")
@click.pass_context
def stdpub(ctx):
    '''
    A PUB that reads stdin.  Eg:

        herbstclient --idle | hh stdpub

    Which is equivalent to using "hh hcpub"
    '''
    socket = make_socket(zmq.PUB, **ctx.obj)
    for line in sys.stdin:
        line = line.strip()
        click.echo(line)
        socket.send_string(line)

@cli.command("pullpub")
@click.argument("inbox")
@click.pass_context
def pullpub(ctx, inbox):
    '''
    Accept on a PULL send out a PUB.

    PULL will bind on the pulladdr.
    '''
    pub = make_socket(zmq.PUB, **ctx.obj)
    pull = make_socket(zmq.PULL, "bind", (inbox,))
    while True:
        line = pull.recv_string()
        click.echo(line)
        pub.send_string(line)


@cli.command("stdsub")
@click.pass_context
def stdsub(ctx):
    '''
    A SUB that write to stdout. 
    '''
    socket = make_socket(zmq.SUB, **ctx.obj)
    while True:
        line = socket.recv_string()
        sys.stdout.write(line + '\n')

@cli.command("onepub")
@click.argument("line")
@click.pass_context
def onepub(ctx, line):
    socket = make_socket(zmq.PUB, **ctx.obj)
    print(line)
    time.sleep(0.1)
    got = socket.send_string(line)

    
@cli.command("onepush")
@click.argument("addr")
@click.argument("line")
def onepush(addr, line):
    socket = make_socket(zmq.PUSH, "connect", (addr,))
    print(line)
    got = socket.send_string(line)



def main():
    cli(obj=None)

if '__main__' == __name__:
    main()

