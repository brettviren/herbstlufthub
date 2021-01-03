#!/usr/bin/env python3
'''Main CLI to herbstlufthub, use "hh" from the shell command line.

There are various sockets involved and they are specified in a uniform
manner.  It extends the usual ZeroMQ address to add URL query
parameters:

- link :: specify to connect or bind the address
- topic :: specify a subscription prefix topic (useful only for SUB)

For example:

  ipc://hhub.ipc?link=bind&topic=window_title_changed&topic=focus_changed

The base "hh" command takes -l/--link options, each specifying one URL
to be used by the "primary socket" of the subsequent command.  Most
commands have as their primary socket either a PUB or a SUB which
comprises the "hub" of herbstlufthub.  But, some commands (eg
"onepush") will have a "primary socket" with a different purpose and
will still use this -l/--link.  Others may additional sockets
specified by options or arguments to their subcommand (eg, "pullpub").

'''
import os
import sys
import zmq
import time
import click
import select
import subprocess
from collections import defaultdict

from string import Formatter
from urllib.parse import urlparse, ParseResult, parse_qs

workdir = os.getenv("HOME","/tmp")

# The "hub" addresses are where the pub/sub network forms.
# Here gives the defaults.
default_hub_links = (
    f'ipc://{workdir}/hhub.ipc',
)

# The "push" is used as a special input (see "pullpub" command) which
# is more robust for one-shot events than a direct pub.
default_push_links = (
    f'ipc://{workdir}/hpush.ipc',
)

# SUBs need topic to subscribe.  One may filter but by default they
# take messages from all topics.
all_topics = ("",)

def parse_url(url):
    '''
    Return (url, query) with query as dict and stripped from URL
    '''
    p = urlparse(url)
    q = parse_qs(p.query)
    if p.netloc:                # tcp
        d = p._asdict()
        d.pop("query")
        url = ParseResult(query='', **d).geturl()
    else:                       # ipc, inproc
        # geturl() doesn't preserve '//' if no netloc
        url = '%s://%s' % (p.scheme, p.path)

    return (url, q)

class Port:
    '''
    Add functionality to a socket.
    '''

    def __init__(self, name, sock):
        self.name = name
        self.sock = sock

    def link(self, url, deflink='connect'):
        '''
        Link port to a URL endpoint.  

        If no link=(bind|connect) in URL query params use deflink.
        '''
        addr, query = parse_url(url)

        topics = query.get("topics", [])
        for topic in topics:
            self.subscribe(topic)
        if not topics:
            self.subscribe()

        links = query.get("link", [deflink])
        sys.stderr.write(f'\tport {self.name}, addr {addr}, links {links}\n')
        if "connect" in links:
            self.sock.connect(addr)
        if "bind" in links:
            self.sock.bind(addr)


    def subscribe(self, topic=""):
        if self.sock.TYPE != zmq.SUB: # give more meaningful error
            raise ValueError(f'port {self.name} not SUB can not subscribe')
        self.sock.subscribe(topic)

class Node:
    '''
    Collect some sockets as named ports on a node
    '''

    def __init__(self):
        self.zctx = zmq.Context()
        self.ports = dict();
        
    def __getattr__(self, name):
        return self.ports[name]

    def port(self, name, ztype):
        '''
        Make and return named socket.
        '''
        try:
            return self.ports[name]
        except KeyError:
            pass
        port = Port(name, self.zctx.socket(ztype))
        self.ports[name] = port
        return port
        

def node_with_port(ztype, links=(), portname='link', deflink='connect'):
    '''
    Return a node primied with one linked up port
    '''
    links = links or default_hub_links
    sys.stderr.write(f'node_with_port: name:{portname} ztype:{ztype} links:{links}\n')
    node = Node()
    port = node.port(portname, ztype)
    for link in links:
        sys.stderr.write(f'\tlink:{link}\n')
        port.link(link, deflink)
    return node


@click.group()
@click.option("-l","--link", multiple=True,
              help="Set a link URL")
@click.pass_context
def cli(ctx, link):
    '''
    herbstlufthub MPMC pub/sub
    '''
    ctx.obj = dict(links = link)
        


@cli.command("hcpub")
@click.option("-c", "--command",
              default="herbstclient --idle",
              help="The command providing events as lines on stdout")
@click.pass_context
def hcpub(ctx, command):
    '''
    A PUB providing events from a herbstclient.
    '''
    node = node_with_port(zmq.PUB, ctx.obj['links'], deflink='bind')
    proc = subprocess.Popen(command,
                            shell=True,
                            universal_newlines=True,
                            text=True, bufsize=1,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    while True:
        line = proc.stdout.readline()
        sys.stderr.write(line)
        line = line.strip()
        node.link.sock.send_string(line)


@cli.command("stdpub")
@click.pass_context
def stdpub(ctx):
    '''
    A PUB that reads events as lines on stdin.
    '''
    node = node_with_port(zmq.PUB, ctx.obj['links'])
    for line in sys.stdin:
        line = line.strip()
        click.echo(line)
        node.link.sock.send_string(line)


@cli.command("pullpub")
@click.argument("inputs", nargs=-1)
@click.pass_context
def pullpub(ctx, inputs):
    '''
    A PUB that receives events on a PULL.
    '''
    node = node_with_port(zmq.PUB, ctx.obj['links'], deflink='bind')
    inbox = node.port("inbox", zmq.PULL)
    links = inputs or default_push_links
    for link in links:
        inbox.link(link, 'bind')
    pull = inbox.sock
    pub = node.link.sock    

    while True:
        line = pull.recv_string()
        click.echo(line)
        pub.send_string(line)


@cli.command("onepush")
@click.option("-t","--timeout", default=0,
              help="Time in milliseconds to wait for push to complete")
@click.argument("event", nargs=-1)
@click.pass_context
def onepush(ctx, timeout, event):
    '''
    Push one event which is given on the command line.

    The -l/--link is for the PULL socket, not the usual PUB/SUB.
    '''
    if not event:
        raise RuntimeError("onepush: no event given")
    msg = '\t'.join(event)
    links = ctx.obj.get('links', ()) or default_push_links
    node = node_with_port(zmq.PUSH, links, deflink='connect')
    node.link.sock.SNDTIMEO = timeout
    try:
        node.link.sock.send_string(msg)
    except zmq.error.Again:
        sys.stderr.write(f'push not sent due to timeout {timeout} ms\n')


@cli.command("onepub")
@click.argument("event", nargs=-1)
@click.pass_context
def onepub(ctx, event):
    '''Send a single event command from a PUB.

    This only makes to use with a connect.  A sleep is required for
    slow subscriber syndrome.

    '''
    if not event:
        raise RuntimeError("onepub: no event given")
    msg = '\t'.join(event)

    node = node_with_port(zmq.PUB, ctx.obj['links'])
    time.sleep(0.1)             # help slow subscriber syndrome
    node.link.sock.send_string(msg)


@cli.command("stdsub")
@click.pass_context
def stdsub(ctx):
    '''
    Receive event messages from a SUB and write them to stdout.
    '''
    node = node_with_port(zmq.SUB, ctx.obj['links'])
    while True:
        # sys.stderr.write("receiving:\n")
        line = node.link.sock.recv_string()
        sys.stdout.write(line + '\n')
    
class SimpleTransform:
    def __init__(self, pattern):
        self.pattern = pattern
        data = dict()
        for one in Formatter().parse(pattern):
            key = one[1]
            if key is None:
                continue
            data[key] = ''
        self.data = data

    def __call__(self, *args):
        etype = args[0]

        gotone = False
        for ind, part in enumerate(args):
            key = f'{etype}_{ind}'
            if key in self.data:
                gotone = True
                self.data[key] = part
        if not gotone:
            return

        line = self.pattern.format(**self.data)
        if not line.endswith('\n'):
            line += '\n'
        return line

def transform_pipe(sock, transform, command):
    shell = False
    if isinstance(command, str):
        shell = True
    proc = subprocess.Popen(command,
                            shell=shell,
                            universal_newlines=True,
                            text=True, bufsize=1,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)

    poller = select.poll()
    poller.register(proc.stdout, select.POLLIN)
    #poller.register(proc.stderr, select.POLLIN)

    last_line = None
    while True:
        event = sock.recv_string()
        sys.stderr.write(f'event: {event}\n')
        line = transform(*event.split('\t'))
        if line is None:
            #sys.stderr.write(f'unused: {event}\n')
            continue
        if line == last_line:
            #sys.stderr.write(f'unchanged: {event}\n')
            continue
        last_line = line
        sys.stderr.write(line)
        proc.stdin.write(line)

        got = poller.poll(0.1)
        if not got:
            continue
        out = proc.stdout.readline()
        sys.stderr.write(f'got back: {out}\n')


@cli.command("subpipe")
@click.option("-p", "--pattern",
              default='',
              help="String format for input to panel command")
@click.argument("command", nargs=-1)
@click.pass_context
def subpipe(ctx, pattern, command):
    '''Transform events from SUB via pattern to command stdin.

    Events are collected into a dictionary keyed by event type.  When
    an event is received, the dictionary is updated and applied to the
    pattern.  If the resulting string is new then it is written to the
    command's stdin.

    Example:

      hh subpipe -p "focus:{focus_changed_1} title:{window_title_changed_1}" cat 

    '''

    node = node_with_port(zmq.SUB, ctx.obj['links'])
    transform_pipe(node.link.sock, SimpleTransform(pattern), command)


def hc(*args):
    cmd = ["herbstclient"]+[str(a) for a in args]
    out = subprocess.run(cmd, capture_output=True)
    return out.stdout.decode().strip()

class DzenTransform:

    tag_names="ABCDEFHIJKLMNOPQRSTUVWXYZ"

    def __init__(self):
        self.data = defaultdict(str)

    def fmt_tags(self):

        selbg=hc("get","window_border_active_color")
        selfg='#101010'
        separator=f'^bg()^fg({selbg})|'

        tag_status = [ts.strip() for ts in 
                      hc("tag_status","0").split('\t') if ts.strip()]
        chunks = list()
        for ind,ts in enumerate(tag_status):
            status, name = ts
            alias = self.tag_names[ind]
            try:
                sfmt = {
                    '#': f"^bg({selbg})^fg({selfg})",
                    '+':  "^bg(#9CA668)^fg(#141414)",
                    ':':  "^bg()^fg(#ffffff)",
                    '!':  "^bg(#FF0675)^fg(#141414)"
                }[status]
            except KeyError:
                sfmt = "^bg()^fg(#ababab)"
            cfmt = f'^ca(1,herbstclient focus_monitor "0" && herbstclient use "{ind+1}") {alias} ^ca()'

            chunks.append(sfmt + cfmt)
        chunks.append(separator)
        return "".join(chunks)

    def fmt_data(self):
        return "^bg()^fg() {title}".format(**self.data)
    
    def fmt(self):
        return self.fmt_tags() + self.fmt_data()

    def __call__(self, *args):
        etype = args[0]
        if etype in ("window_title_changed","focus_changed"):
            if args[1] == "0x0":
                return
            self.data["window"] = args[1]
            self.data["title"] = args[2]

        return self.fmt() + '\n'

@cli.command("subdzen")
@click.pass_context
def subdzen(ctx):
    '''Connect a SUB to dzen2'''
    sx, sy, sw, sh = hc("monitor_rect", 0).split()
    px, py, pw, ph = sx, sy, sw, "32"

    font="-*-fixed-medium-*-*-*-24-*-*-*-*-*-*-*"
    buttons="button3=;button4=exec:'herbstclient use_index -1';button5=exec:'herbstclient use_index +1'"
    bgcolor=""
    fgcolor='#efefef'
    bgcolor=hc("get","frame_border_normal_color")

    command=["dzen2",
             "-x", px, "-y", py, "-w", pw, "-h", ph,
             "-fn", font,
             "-e", buttons,
             "-ta","l", "-bg", bgcolor, "-fg", fgcolor]


    node = node_with_port(zmq.SUB, ctx.obj['links'])
    transform_pipe(node.link.sock, DzenTransform(), command)
    


def main():
    cli(obj=None)

if '__main__' == __name__:
    main()

