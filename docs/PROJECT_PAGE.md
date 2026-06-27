# Sound Safari: I taught my kids to sample the whole world

A few weeks ago I handed my 10-year-old an iPad with a studio mic clipped to it and told him to go record anything that sounded interesting. His little brother, who's 7, immediately wanted in. So now I've got two kids crouched over a storm drain, a screen door, a bowl of cereal, the dog, recording all of it like a couple of tiny field producers.

The idea was simple and a little selfish: I make electronic music in Ableton Live, and there's nothing in a factory sample pack that sounds like *your own kid knocking on a piece of driftwood*. I wanted a giant library of custom one-shots. The kids wanted to run around the yard making noise. Win together.

The problem showed up about ten minutes later, when I copied a folder of recordings off the iPad and realized I had a glorious mess. Long takes. Forty sounds crammed into one file with random gaps. Compressed `.m4a` junk. The classic "raw recordings in a single directory" situation that every sampler knows and dreads. Chopping all of that apart by hand and naming it would take an entire weekend, and I'd rather spend the weekend, you know, actually making music.

So I built a tool. It's called **chopshop** (the kids call it Sound Safari), and it's open source. [It lives on GitHub](https://github.com/JosephSchafer/chopshop).

## What it actually does

Truth be told, this is three boring problems stitched together into one fun one.

**It slices.** Point it at a folder of recordings and it finds every individual sound inside the long takes - the attack of each hit, where the tail fades into silence - and carves them into clean little one-shots. That part is mostly [librosa](https://librosa.org/) doing onset detection while I tune the knobs.

**It sorts.** Here's the part I'm proud of. It uses a zero-shot audio model ([LAION-CLAP](https://github.com/LAION-AI/CLAP)) to listen to each slice and *guess what it is* - water, metal, wood, glass, a voice, an animal - using plain-English descriptions instead of some fixed drum-machine vocabulary. No training, no labeling thousands of examples. You just tell it the categories you care about and it picks the best match. For sounds "found in the world," that's exactly the right tool.

**It lets the kids be the boss.** This is the bit that makes it actually work. The AI only *guesses*. Then a little web app opens in the browser, and my 10-year-old goes through the sounds one at a time: hit play, look at the waveform, tap the right emoji if the robot got it wrong, give it a fun name ("My First Splash" is a real one now), keep it or toss it. Big buttons, no code, spacebar to play, Enter to keep. He flies through it.

I went back and forth on whether to just let the AI auto-sort everything unattended. I'm glad I didn't. Watching a 10-year-old overrule the computer because *he* knows that's the screen door and not "metal" - that's the whole project, honestly. He's learning to trust his ears, and the library comes out better for it.

## The Ableton part, which was fussier than I expected

Getting sounds into a folder is easy. Getting them into Ableton Live 11 *the way an artist actually wants them* is where I lost an evening.

The tool publishes a clean folder-per-category library you can drop straight into Live's browser. Every WAV gets real metadata baked inside the file using open standards (BWF and RIFF INFO tags), so the labels travel with the sound even if you open it in Logic or Reaper or just hover over it in Explorer. I didn't want this locked to Ableton forever.

Then it builds **Drum Racks** - one per category - so you drag "Water.adg" onto a track and suddenly every water sound my kids found is mapped across the pads, instantly playable. That `.adg` format is just gzipped XML once you crack it open, which sounds simple right up until you learn that Ableton stores the sample paths *inside* the rack. Build it on my desktop and the paths point at my C: drive. Open it on a laptop and... media offline. Sad trombone.

So the racks now use relative paths that walk from the rack to the sample, which means the whole library is portable. Copy it, sync it, hand it to one of the boys on a different machine, and the racks still find their sounds. I'll be the first to admit I haven't yet confirmed every edge case loads perfectly in a fresh Live install - that's the next thing on my list - but the structure's right and it survives moving between computers, which was the hard part.

## On backups, because everyone always asks

The sounds live in a Google Drive folder, so they back up and the family can share them without anybody thinking about it. The code lives in Git, because code wants version history and Drive will happily corrupt a file you saved mid-edit. One thing I learned the slightly annoying way: you have to mark the Drive folder "available offline," or Ableton tries to read zero-byte placeholder files and throws a fit. The working scratch files stay off Drive entirely so the sync client isn't fighting me while the kids are still trimming.

## Why it's open source

Because somebody else out there has a pile of weird recordings and a copy of Ableton, and there's no reason for them to spend the weekend I almost spent. It's MIT licensed. The whole thing is documented - there's an architecture writeup, a full command reference, a kids' guide written *for the kids*, and notes on the Ableton internals if you want to poke at the `.adg` format yourself.

A couple of plain takeaways if you're thinking about building something similar:

1. Let the AI guess and the human decide. Don't automate away the fun part.
2. Open standards in the file beat a clever database every time. The metadata should travel with the sound.
3. Relative paths. Always relative paths. Ask me how I know.

If you want to try it, [grab it on GitHub](https://github.com/JosephSchafer/chopshop). And if you build something with it - especially if your kids make the sounds - reach out and tell me about it. I'd love to hear what a stranger's storm drain sounds like.
