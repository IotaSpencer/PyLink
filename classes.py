class IrcUser():
    def __init__(self, nick, ts, uid, ident='null', host='null',
                 realname='PyLink dummy client', realhost='null',
                 ip='0.0.0.0'):
        self.nick = nick
        self.ts = ts
        self.uid = uid
        self.ident = ident
        self.host = host
        self.realhost = realhost
        self.ip = ip
        self.realname = realname

        self.identified = False

    def __repr__(self):
        return repr(self.__dict__)

class IrcServer():
    def __init__(self, uplink):
        self.uplink = uplink
        self.users = []
    def __repr__(self):
        return repr(self.__dict__)
