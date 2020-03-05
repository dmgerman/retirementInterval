#!/usr/bin/env python

import enum
import operator
import os
import re
import time
import types
import unicodedata
import urllib.parse
from functools import reduce

from aqt import mw
from aqt.utils import showInfo, showCritical
from aqt.webview import AnkiWebView
from aqt.qt import (Qt, QAction, QStandardPaths,
                    QImage, QPainter, QSize, QEvent, QSizePolicy,
                    QFileDialog, QDialog, QHBoxLayout, QVBoxLayout, QGroupBox,
                    QLineEdit, QLabel, QCheckBox, QSpinBox, QComboBox, QPushButton)

class RetirementIntervalWebView(AnkiWebView):
    def __init__(self, parent=None):
        super().__init__()
        # Saved images are empty if the background is transparent; AnkiWebView
        # sets bg color to transparent by default
        self._page.setBackgroundColor(Qt.white)

class RetirementInterval:
    def __init__(self, mw):
        # save the cursor
        if mw:
            self.menuAction = QAction("Retirement Interval", mw, triggered=self.setup)
            mw.form.menuTools.addSeparator()
            mw.form.menuTools.addAction(self.menuAction)

    def results(self, c, interval):
        # i don't get it why anki runs in a single transactions. This means that if the add-on breaks, it will
        # taking along the reviews of the user.
        # so let us commit. At this point, things should be "safe" for the user to have the reviews committed

        # we'll run in a transactions so we do not alter the database, easier than removing the tables
        c.execute("commit")

        c.execute("begin")

        # why compute in python when the database can do all the work with less potential for error?

        # note that the create reviews table receives as a parameter the interval
        # we could have used the current time, but it is probably more useful to use the time of last review

        c.execute("""
drop table if exists button;
""")

        c.execute("""
drop table if exists deckinfo;
""")

        c.execute("""
create table deckinfo as
        SELECT value as deckname,
        substr(substr(r.fullkey, 3), 0, instr(substr(r.fullkey, 3), '.'))  as did,
        r.fullkey from col, json_tree(col.decks) as r
        where r.key = 'name';
""")

        c.execute("""
create temp table button (type int, ease int, ename text);
""")

        c.execute("""
insert into button values (0, 1, "learn/wrong"), (0, 2, "learn/ok"), (0, 3, "learn/easy"),
                          (1, 1, "review/wrong"), (1, 2, "review/hard"), (1, 3, "review/ok"), (1, 4, "review/easy"),
                          (2, 1, "relearn/wrong"), (2, 2, "relearn/ok"), (0, 2, "relearn/easy"),
                          (3, 1, "cram/wrong"), (3, 2, "cram/hard"), (3, 3, "cram/ok"), (3, 4, "cram/easy");
""")


        r = c.all("""
with summary as (
     select deckname, ename, r.lastivl, r.type, ease, count(*) as nreviews from revlog r
     join cards c on (r.cid = c.id) join deckinfo d using (did) natural left join button
     where
    r.lastivl > ? and queue >0
    group by deckname, ename, r.lastivl, r.type
    order by r.lastivl, ename,  r.type),
-- cards reviewed with interval larger than X
sumcards as (
   select deckname, count(distinct r.cid) as ncards from
      revlog r join cards c on (r.cid = c.id) join deckinfo d using (did)
      where r.lastivl > ? and queue > 0
      group by deckname),
-- all cards in the review queue
allcards as (
   select deckname, count(distinct r.cid) as allcards from
      revlog r join cards c on (r.cid = c.id) join deckinfo d using (did)
      where queue > 0
      group by deckname),
-- cards in the deck that are in the review queue with interval larger than what we want
-- this would be the number of cards retired
elegcards as (
   select deckname, count(distinct id) as elegible from
     cards c  join deckinfo d using (did)
     where ivl > ? and queue > 0
     group by deckname),
-- failed reviews
bad as (
   select deckname, ename, sum(nreviews) as bad from
      summary where ename in ('review/wrong')
      group by deckname, ename),
-- passed reviews
good as (
   select deckname, ename, sum(nreviews) as good from
      summary where ename not in ('review/wrong')
      group by deckname)

select *,
   printf('%.2f', good*1.00/(good+coalesce(bad, 0)))
     as prop
 from allcards left join elegcards using (deckname)
          left join sumcards using (deckname) left join good using (deckname)  left  join bad using (deckname)
order by good*1.0/(good + bad);
""", interval, interval, interval)


        return r

    def compute(self, config):

        self.win = QDialog(mw)
        self.wv = RetirementIntervalWebView()
        vl = QVBoxLayout()
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.wv)
        r = self.results(config.cursor, config.interval)

        header = """<tr>
<td><b>Deck</b></td>
<td><b>Active cards in deck</b></td>
<td><b>Eligible cards to retire</b></td>
<td><b>Cards reviewed</b></td>
<td><b>Button pressed Pass</b></td>
<td><b>Cards Passed</b></td>
<td><b>Button pressed Fail</b></td>
<td><b>Cards Failed</b></td>
<td><b>Proportion passed</b></td></tr>
"""
        mystr = "<tr>"+ "</tr><br><tr>".join("<td>" +  "</td><td>".join(str(col) for col in tuple) + "</td>" for tuple in r ) + "</tr>"
        self.html = "<h2>Review success of cards with interval larger than " + str(config.interval) + " days</h2>\n"  + "<table>" + header + mystr + "</table>"

        self.wv.stdHtml(self.html)
        hl = QHBoxLayout()
        vl.addLayout(hl)
        bb = QPushButton("Close", clicked=self.win.reject)
        hl.addWidget(bb)
        self.win.setLayout(vl)
        self.win.resize(800, 400)

        return 0



    def setup(self):
        addonconfig = mw.addonManager.getConfig(__name__)
        config = types.SimpleNamespace(**addonconfig['defaults'])
        config.cursor = mw.col.db

        swin = QDialog(mw)

        vl = QVBoxLayout()
        fl = QHBoxLayout()
        frm = QGroupBox("Settings")
        vl.addWidget(frm)
        il = QVBoxLayout()
        fl = QHBoxLayout()
        stint = QSpinBox()
        stint.setRange(1, 65536)
        stint.setValue(config.interval)
        il.addWidget(QLabel("Number of days for retirement interval:"))
        il.addWidget(stint)
        frm.setLayout(il)

        hl = QHBoxLayout()
        vl.addLayout(hl)
        gen = QPushButton("Generate", clicked=swin.accept)
        hl.addWidget(gen)
        cls = QPushButton("Close", clicked=swin.reject)
        hl.addWidget(cls)
        swin.setLayout(vl)
        swin.setTabOrder(gen, cls)
        swin.setTabOrder(stint, gen)
        swin.resize(500, 200)
        if swin.exec_():
            mw.progress.start(immediate=True)
            config.interval = stint.value()
            self.compute(config)
            mw.progress.finish()
            self.win.show()

if __name__ != "__main__":
    if mw:
        mw.retirementInterval = RetirementInterval(mw)
else:
    print("This is an addon for the Anki spaced repetition learning system and cannot be run directly.")
    print("Please download Anki from <http://ankisrs.net/>")

# vim:expandtab:
