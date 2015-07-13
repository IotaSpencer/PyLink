# relay.py: PyLink Relay plugin
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pickle
import sched
import threading
import time
import string
from collections import defaultdict

import utils
from log import log

dbname = "pylinkrelay.db"
relayusers = defaultdict(dict)
queued_users = []

def normalizeNick(irc, netname, nick, separator="/"):
    orig_nick = nick
    protoname = irc.proto.__name__
    maxnicklen = irc.maxnicklen
    if protoname == 'charybdis':
        # Charybdis doesn't allow / in usernames, and will quit with
        # a protocol violation if there is one.
        separator = separator.replace('/', '|')
        nick = nick.replace('/', '|')
    if nick.startswith(tuple(string.digits)):
        # On TS6 IRCd-s, nicks that start with 0-9 are only allowed if
        # they match the UID of the originating server. Otherwise, you'll
        # get nasty protocol violations!
        nick = '_' + nick
    tagnicks = True

    suffix = separator + netname
    nick = nick[:maxnicklen]
    # Maximum allowed length of a nickname.
    allowedlength = maxnicklen - len(suffix)
    # If a nick is too long, the real nick portion must be cut off, but the
    # /network suffix must remain the same.

    nick = nick[:allowedlength]
    nick += suffix
    while utils.nickToUid(irc, nick):
        # The nick we want exists? Darn, create another one then.
        # Increase the separator length by 1 if the user was already tagged,
        # but couldn't be created due to a nick conflict.
        # This can happen when someone steals a relay user's nick.
        new_sep = separator + separator[-1]
        nick = normalizeNick(irc, netname, orig_nick, separator=new_sep)
    finalLength = len(nick)
    assert finalLength <= maxnicklen, "Normalized nick %r went over max " \
        "nick length (got: %s, allowed: %s!" % (nick, finalLength, maxnicklen)

    return nick

def loadDB():
    global db
    try:
        with open(dbname, "rb") as f:
            db = pickle.load(f)
    except (ValueError, IOError):
        log.exception("Relay: failed to load links database %s"
            ", creating a new one in memory...", dbname)
        db = {}

def exportDB(scheduler):
    scheduler.enter(30, 1, exportDB, argument=(scheduler,))
    log.debug("Relay: exporting links database to %s", dbname)
    with open(dbname, 'wb') as f:
        pickle.dump(db, f, protocol=4)

def findRelay(chanpair):
    if chanpair in db:  # This chanpair is a shared channel; others link to it
        return chanpair
    # This chanpair is linked *to* a remote channel
    for name, dbentry in db.items():
        if chanpair in dbentry['links']:
            return name

def initializeChannel(homeirc, channel):
    homeirc.proto.joinClient(homeirc, homeirc.pseudoclient.uid, channel)

def handle_join(irc, numeric, command, args):
    channel = args['channel']
    if not findRelay((irc.name, channel)):
        # No relay here, return.
        return
    modes = args['modes']
    ts = args['ts']
    users = set(args['users'])
    users.update(irc.channels[channel].users)
    for user in users:
        try:
            if irc.users[user].remote:
                # Is the .remote atrribute set? If so, don't relay already
                # relayed clients; that'll trigger an endless loop!
                continue
        except AttributeError:  # Nope, it isn't.
            pass
        if user == irc.pseudoclient.uid:
            # We don't need to clone the PyLink pseudoclient... That's
            # meaningless.
            continue
        userobj = irc.users[user]
        userpair_index = relayusers.get((irc.name, user))
        ident = userobj.ident
        host = userobj.host
        realname = userobj.realname
        log.debug('Okay, spawning %s/%s everywhere', user, irc.name)
        for name, remoteirc in utils.networkobjects.items():
            nick = normalizeNick(remoteirc, irc.name, userobj.nick)
            if name == irc.name:
                # Don't relay things to their source network...
                continue
            # If the user (stored here as {(netname, UID):
            # {network1: UID1, network2: UID2}}) exists, don't spawn it
            # again!
            u = None
            if userpair_index is not None:
                u = userpair_index.get(remoteirc.name)
            if u is None:  # .get() returns None if not found
                u = remoteirc.proto.spawnClient(remoteirc, nick, ident=ident,
                                                host=host, realname=realname).uid
                remoteirc.users[u].remote = irc.name
            log.debug('(%s) Spawning client %s (UID=%s)', irc.name, nick, u)
            relayusers[(irc.name, userobj.uid)][remoteirc.name] = u
            remoteirc.users[u].remote = irc.name
            remoteirc.proto.joinClient(remoteirc, u, channel)
    '''
    chanpair = findRelay((homeirc.name, channel))
    all_links = [chanpair] + list(db[chanpair]['links'])
    # Iterate through all the (network, channel) pairs related
    # to the channel.
    log.debug('all_links: %s', all_links)
    for link in all_links:
        network, channel = link
        if network == homeirc.name:
            # Don't process our own stuff...
            continue
        log.debug('Processing link %s (homeirc=%s)', link, homeirc.name)
        try:
            linkednet = utils.networkobjects[network]
        except KeyError:
            # Network isn't connected yet.
            continue
        # Iterate through each of these links' channels' users
        for user in linkednet.channels[channel].users.copy():
            log.debug('Processing user %s/%s (homeirc=%s)', user, linkednet.name, homeirc.name)
            if user == linkednet.pseudoclient.uid:
                # We don't need to clone the PyLink pseudoclient... That's
                # meaningless.
                continue
            try:
                if linkednet.users[user].remote:
                    # Is the .remote atrribute set? If so, don't relay already
                    # relayed clients; that'll trigger an endless loop!
                    continue
            except AttributeError:  # Nope, it isn't.
                pass
            userobj = linkednet.users[user]
            userpair_index = relayusers.get((linkednet.name, user))
            ident = userobj.ident
            host = userobj.host
            realname = userobj.realname
            # And a third for loop to spawn+join pseudoclients for
            # them all.
            log.debug('Okay, spawning %s/%s everywhere', user, linkednet.name)
            for name, irc in utils.networkobjects.items():
                nick = normalizeNick(irc, linkednet.name, userobj.nick)
                if name == linkednet.name:
                    # Don't relay things to their source network...
                    continue
                # If the user (stored here as {(netname, UID):
                # {network1: UID1, network2: UID2}}) exists, don't spawn it
                # again!
                u = None
                if userpair_index is not None:
                    u = userpair_index.get(irc.name)
                if u is None:  # .get() returns None if not found
                    u = irc.proto.spawnClient(irc, nick, ident=ident,
                                          host=host, realname=realname).uid
                    irc.users[u].remote = linkednet.name
                log.debug('(%s) Spawning client %s (UID=%s)', irc.name, nick, u)
                relayusers[(linkednet.name, userobj.uid)][irc.name] = u
                irc.proto.joinClient(irc, u, channel)
    '''

def removeChannel(irc, channel):
    if channel not in map(str.lower, irc.serverdata['channels']):
        irc.proto.partClient(irc, irc.pseudoclient.uid, channel)

def relay(homeirc, func, args):
    """<source IRC network object> <function name> <args>

    Relays a call to <function name>(<args>) to every IRC object's protocol
    module except the source IRC network's."""
    for name, irc in utils.networkobjects.items():
        if name == homeirc.name:
            continue
        f = getattr(irc.proto, func)
        f(*args)

@utils.add_cmd
def create(irc, source, args):
    """<channel>

    Creates the channel <channel> over the relay."""
    try:
        channel = args[0].lower()
    except IndexError:
        utils.msg(irc, source, "Error: not enough arguments. Needs 1: channel.")
        return
    if not utils.isChannel(channel):
        utils.msg(irc, source, 'Error: invalid channel %r.' % channel)
        return
    if source not in irc.channels[channel].users:
        utils.msg(irc, source, 'Error: you must be in %r to complete this operation.' % channel)
        return
    if not utils.isOper(irc, source):
        utils.msg(irc, source, 'Error: you must be opered in order to complete this operation.')
        return
    db[(irc.name, channel)] = {'claim': [irc.name], 'links': set(), 'blocked_nets': set()}
    initializeChannel(irc, channel)
    utils.msg(irc, source, 'Done.')
utils.add_hook(handle_join, 'JOIN')

@utils.add_cmd
def destroy(irc, source, args):
    """<channel>

    Destroys the channel <channel> over the relay."""
    try:
        channel = args[0].lower()
    except IndexError:
        utils.msg(irc, source, "Error: not enough arguments. Needs 1: channel.")
        return
    if not utils.isChannel(channel):
        utils.msg(irc, source, 'Error: invalid channel %r.' % channel)
        return
    if not utils.isOper(irc, source):
        utils.msg(irc, source, 'Error: you must be opered in order to complete this operation.')
        return

    if (irc.name, channel) in db:
        del db[(irc.name, channel)]
        removeChannel(irc, channel)
        utils.msg(irc, source, 'Done.')
    else:
        utils.msg(irc, source, 'Error: no such relay %r exists.' % channel)
        return

@utils.add_cmd
def link(irc, source, args):
    """<remotenet> <channel> <local channel>

    Links channel <channel> on <remotenet> over the relay to <local channel>.
    If <local channel> is not specified, it defaults to the same name as
    <channel>."""
    try:
        channel = args[1].lower()
        remotenet = args[0].lower()
    except IndexError:
        utils.msg(irc, source, "Error: not enough arguments. Needs 2-3: remote netname, channel, local channel name (optional).")
        return
    try:
        localchan = args[2].lower()
    except IndexError:
        localchan = channel
    for c in (channel, localchan):
        if not utils.isChannel(c):
            utils.msg(irc, source, 'Error: invalid channel %r.' % c)
            return
    if source not in irc.channels[localchan].users:
        utils.msg(irc, source, 'Error: you must be in %r to complete this operation.' % localchan)
        return
    if not utils.isOper(irc, source):
        utils.msg(irc, source, 'Error: you must be opered in order to complete this operation.')
        return
    if remotenet not in utils.networkobjects:
        utils.msg(irc, source, 'Error: no network named %r exists.' % remotenet)
        return
    if (irc.name, localchan) in db:
        utils.msg(irc, source, 'Error: channel %r is already part of a relay.' % localchan)
        return
    for dbentry in db.values():
        if (irc.name, localchan) in dbentry['links']:
            utils.msg(irc, source, 'Error: channel %r is already part of a relay.' % localchan)
            return
    try:
        entry = db[(remotenet, channel)]
    except KeyError:
        utils.msg(irc, source, 'Error: no such relay %r exists.' % channel)
        return
    else:
        entry['links'].add((irc.name, localchan))
        initializeChannel(irc, localchan)
        utils.msg(irc, source, 'Done.')

@utils.add_cmd
def delink(irc, source, args):
    """<local channel> [<network>]

    Delinks channel <local channel>. <network> must and can only be specified
    if you are on the host network for <local channel>, and allows you to
    pick which network to delink. To remove all networks from a relay, use the
    'destroy' command instead."""
    try:
        channel = args[0].lower()
    except IndexError:
        utils.msg(irc, source, "Error: not enough arguments. Needs 1-2: channel, remote netname (optional).")
        return
    try:
        remotenet = args[1].lower()
    except IndexError:
        remotenet = None
    if not utils.isOper(irc, source):
        utils.msg(irc, source, 'Error: you must be opered in order to complete this operation.')
        return
    if not utils.isChannel(channel):
        utils.msg(irc, source, 'Error: invalid channel %r.' % channel)
        return
    for dbentry in db.values():
        if (irc.name, channel) in dbentry['links']:
            entry = dbentry
            break
    if (irc.name, channel) in db:  # We own this channel
        if remotenet is None:
            utils.msg(irc, source, "Error: you must select a network to delink, or use the 'destroy' command no remove this relay entirely.")
            return
        else:
            for entry in db.values():
                for link in entry['links'].copy():
                    if link[0] == remotenet:
                        entry['links'].remove(link)
                        removeChannel(utils.networkobjects[remotenet], link[1])
    else:
        entry['links'].remove((irc.name, channel))
        removeChannel(irc, channel)
    utils.msg(irc, source, 'Done.')

def main():
    loadDB()
    utils.schedulers['relaydb'] = scheduler = sched.scheduler()
    scheduler.enter(30, 1, exportDB, argument=(scheduler,))
    # Thread this because exportDB() queues itself as part of its
    # execution, in order to get a repeating loop.
    thread = threading.Thread(target=scheduler.run)
    thread.daemon = True
    thread.start()
    '''
        # Same goes for all the other initialization stuff; we only
        # want it to happen once.
        for network, ircobj in utils.networkobjects.items():
            if ircobj.name != irc.name:
                irc.proto.spawnServer(irc, '%s.relay' % network)
    '''

    for chanpair, entrydata in db.items():
        network, channel = chanpair
        try:
            initializeChannel(utils.networkobjects[network], channel)
            for link in entrydata['links']:
                network, channel = link
                initializeChannel(utils.networkobjects[network], channel)
        except KeyError:
            pass  # FIXME: initialize as soon as the network connects,
                  # not when the next JOIN occurs
