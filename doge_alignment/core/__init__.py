"""The alignment chart without Discord.

Everything a front end needs: the chart and its rules (:mod:`.chart`), storage
and YAML formats (:mod:`.storage`), what to show (:mod:`.view`), where to draw
it (:mod:`.layout`) and the drawing itself (:mod:`.render`, ImageMagick through
Wand). The Red cog is one front end, ``python -m doge_alignment`` another; a
web app would be a third.

Nothing in this package imports discord.py or Red (a test checks), and apart
from :mod:`.render` and :mod:`.fetch` it is pure functions over immutable data.
"""
