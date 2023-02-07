# bot.py
import os
from dotenv import load_dotenv

# import asyncio
# import threading
# import http.server
# import socketserver
# from urllib.parse import urlparse, parse_qs
import server
from aiohttp import web

import re
import requests
from collections import defaultdict

import discord
from discord.ext import commands
from discord.ext.commands import CommandNotFound

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
PORT = os.getenv('PORT')

bot = commands.Bot(intents=discord.Intents.all(), command_prefix='!')

@bot.event
async def on_ready():
  print(f"{bot.user} has connected to Discord!")
  bot.server = server.HTTPServer(
    bot=bot,
    host="0.0.0.0",
    port="8000",
  )
  await bot.server.start()

@bot.event
async def on_thread_create(th):
  await th.join()

@bot.event
async def on_command_error(ctx, error):
  if isinstance(error, CommandNotFound):
    return
  raise error

@bot.command()
async def ping(ctx):
  await ctx.send(f'Pong! {round(bot.latency * 1000)} ms')

@bot.event
async def on_reaction_add(reaction, user):
  msg = reaction.message
  msg_content = msg.content.replace('*', '')
  role = discord.utils.find(lambda r: r.name == 'Delegate', msg.guild.roles)
  competition = msg.channel.name
  global passcode_db, scramble_stack

  # check if passcodes exist for this channel
  if competition not in passcode_db:
    return

  if role in user.roles and user != bot.user and msg.author == bot.user and parse_passcode(msg_content) and not any(r.emoji == '✅' for r in msg.reactions):
    if reaction.emoji == '➡':
      competition_data = passcode_db[competition]

      # parse message for current set
      event, event_round, scramble_set, passcode = parse_passcode(msg_content)

      # go to next scramble set
      scramble_set += 1

      # check if scramble set exists at comp
      if scramble_set < 0 or scramble_set >= len(competition_data[event][event_round]):
        return

      # get passcode
      passcode = competition_data[event][event_round][scramble_set]

      # manage scramble stack
      if competition in scramble_stack:
        for i in range(1, len(scramble_stack[competition]) + 1):
          m = scramble_stack[competition][-i]
          _m = await msg.channel.fetch_message(m.id)
          if not _m:
            continue
          elif any(r.emoji == '✅' for r in _m.reactions):
            break
          else:
            await m.clear_reactions()
            await m.add_reaction('✅')

      # send code
      new_msg = await send_passcode(msg.channel, event, event_round, scramble_set, passcode)

      # add to scramble stack
      scramble_stack[competition].append(new_msg)

    elif reaction.emoji == '↩️':
      if competition not in scramble_stack or msg not in scramble_stack[competition]:
        return
      await msg.delete()
      scramble_stack[competition].pop(-1)
      if len(scramble_stack[competition]) > 0:
        prev_msg = await msg.channel.fetch_message(scramble_stack[competition][-1].id)
        await prev_msg.clear_reactions()
        await prev_msg.add_reaction('➡')
        await prev_msg.add_reaction('↩️')

@bot.command()
@commands.has_role("Delegate")
async def load(ctx, arg):
  attachments = ctx.message.attachments
  if len(attachments) == 0:
    return
  response = requests.get(attachments[0].url)
  if not response:
    return
  competition = arg.lower()
  competition_data = defaultdict(lambda: defaultdict(list))
  for line in response.iter_lines():
    line = line.decode("utf-8")
    passcode_data = parse_passcode(line)
    if passcode_data:
      event, event_round, scramble_set, passcode = passcode_data
      competition_data[event][event_round].append(passcode)
  global passcode_db
  passcode_db[competition] = competition_data
  await ctx.send(f'Loaded scrambles for `#{competition}`!')

@bot.command()
@commands.has_role("Delegate")
async def clear(ctx, arg):
  global passcode_db
  competition = arg.lower()
  if competition not in passcode_db:
    return
  passcode_db.pop(competition)
  scramble_stack.pop(competition)
  await ctx.send(f'Cleared scrambles for `#{competition}`!')

@bot.command(aliases=['password', 'code', 'pass', 'pw', 'pc'])
@commands.has_role("Delegate")
async def passcode(ctx, *args):
  # check if passcodes exist for this channel
  global passcode_db
  competition = ctx.channel.name
  if competition not in passcode_db:
    return
  competition_data = passcode_db[competition]

  # handle args
  if args[0].lower() not in event_aliases:
    return
  if len(args) == 1:
    event, event_round, scramble_set = event_aliases[args[0].lower()], '1', 0
  elif len(args) == 2:
    if args[1].isnumeric():
      event, event_round, scramble_set = event_aliases[args[0].lower()], args[1], 0
    elif args[1].isalpha():
      event, event_round, scramble_set = event_aliases[args[0].lower()], '1', ord(args[1].lower()) - 97
    else:
      return
  elif len(args) == 3:
    if args[1].isnumeric() and args[2].isalpha():
      event, event_round, scramble_set = event_aliases[args[0].lower()], args[1], ord(args[2].lower()) - 97
    else:
      return

  # check if event, round, and scramble set exists at comp
  if event not in competition_data:
    return
  if event_round not in competition_data[event]:
    return
  if scramble_set < 0 and scramble_set >= len(competition_data[event][event_round]):
    return

  # get passcode
  passcode = competition_data[event][event_round][scramble_set]

  # send code
  msg = await send_passcode(ctx, event, event_round, scramble_set, passcode)

  # start new scramble stack
  global scramble_stack
  if competition in scramble_stack:
    for i in range(1, len(scramble_stack[competition]) + 1):
      m = scramble_stack[competition][-i]
      _m = await ctx.channel.fetch_message(m.id)
      if not _m:
        continue
      elif any(r.emoji == '✅' for r in _m.reactions):
        break
      else:
        await m.clear_reactions()
        await m.add_reaction('✅')
  scramble_stack[competition] = [msg]

@bot.command()
@commands.has_role("Delegate")
async def next(ctx):
  # check if passcodes exist for this channel
  global passcode_db
  competition = ctx.channel.name
  if competition not in passcode_db:
    return
  competition_data = passcode_db[competition]

  global scramble_stack
  if competition not in scramble_stack or len(scramble_stack[competition]) == 0:
    return

  msg = scramble_stack[competition][-1]
  msg_content = msg.content.replace('*', '')

  # parse message for current set
  event, event_round, scramble_set, passcode = parse_passcode(msg_content)

  # go to next scramble set
  scramble_set += 1

  # check if scramble set exists at comp
  if scramble_set < 0 or scramble_set >= len(competition_data[event][event_round]):
    return

  # get passcode
  passcode = competition_data[event][event_round][scramble_set]

  # manage scramble stack
  if competition in scramble_stack:
    for i in range(1, len(scramble_stack[competition]) + 1):
      m = scramble_stack[competition][-i]
      _m = await msg.channel.fetch_message(m.id)
      if not _m:
        continue
      elif any(r.emoji == '✅' for r in _m.reactions):
        break
      else:
        await m.clear_reactions()
        await m.add_reaction('✅')

  # send code
  new_msg = await send_passcode(msg.channel, event, event_round, scramble_set, passcode)

  # add to scramble stack
  scramble_stack[competition].append(new_msg)

@bot.command()
async def info(ctx, *args):
  competition = ctx.channel.name if len(args) == 0 else args[0]
  # check if passcodes exist for this channel
  global passcode_db
  if competition not in passcode_db:
    return
  competition_data = passcode_db[competition]
  await ctx.send('\n'.join('\n'.join(f'**{e} Round {r}**: {len(competition_data[e][r])} sets' for r in competition_data[e].keys()) for e in competition_data.keys()))

@bot.command(aliases=['comps'])
async def competitions(ctx):
  await ctx.send(', '.join(passcode_db.keys()))

async def send_passcode(ctx, event, event_round, scramble_set, passcode):
  msg = await ctx.send(f'**{event} Round {event_round} Attempt {scramble_set + 1}**: {passcode}' if event == "3x3x3 Multiple Blindfolded" or event == "3x3x3 Fewest Moves"
    else f'**{event} Round {event_round} Scramble Set {chr(scramble_set + 65)}**: {passcode}')
  await msg.add_reaction('➡')
  await msg.add_reaction('↩️')
  return msg

re_unigroup = re.compile(r'(?P<event>.+) Round (?P<event_round>\d+): (?P<passcode>\w+)')
re_multigroup = re.compile(r'(?P<event>.+) Round (?P<event_round>\d+) Scramble Set (?P<scramble_set>[A-Z]+): (?P<passcode>\w+)')
re_attempt = re.compile(r'(?P<event>.+) Round (?P<event_round>\d+) Attempt (?P<scramble_set>\d+): (?P<passcode>\w+)')

def parse_passcode(line):
  line = line.strip()
  match = re_unigroup.match(line)
  if match:
    event, event_round, passcode = match.groups()
    return (event, event_round, 0, passcode)
  match = re_multigroup.match(line)
  if match:
    event, event_round, scramble_set, passcode = match.groups()
    return (event, event_round, ord(scramble_set) - 65, passcode)
  match = re_attempt.match(line)
  if match:
    event, event_round, scramble_set, passcode = match.groups()
    return (event, event_round, int(scramble_set) - 1, passcode)

event_aliases = {"3x3x3": "3x3x3",
"3x3": "3x3x3",
"3": "3x3x3",
"333": "3x3x3",
"2x2x2": "2x2x2",
"2x2": "2x2x2",
"2": "2x2x2",
"222": "2x2x2",
"4x4x4": "4x4x4",
"4x4": "4x4x4",
"4": "4x4x4",
"444": "4x4x4",
"5x5x5": "5x5x5",
"5x5": "5x5x5",
"5": "5x5x5",
"555": "5x5x5",
"6x6x6": "6x6x6",
"6x6": "6x6x6",
"6": "6x6x6",
"666": "6x6x6",
"7x7x7": "7x7x7",
"7x7": "7x7x7",
"7": "7x7x7",
"777": "7x7x7",
"3bld": "3x3x3 Blindfolded",
"bld": "3x3x3 Blindfolded",
"3bf": "3x3x3 Blindfolded",
"bf": "3x3x3 Blindfolded",
"333bf": "3x3x3 Blindfolded",
"fmc": "3x3x3 Fewest Moves",
"fm": "3x3x3 Fewest Moves",
"333fm": "3x3x3 Fewest Moves",
"oh": "3x3x3 One-Handed",
"333oh": "3x3x3 One-Handed",
"clock": "Clock",
"clk": "Clock",
"megaminx": "Megaminx",
"mega": "Megaminx",
"minx": "Megaminx",
"pyraminx": "Pyraminx",
"pyra": "Pyraminx",
"pyram": "Pyraminx",
"skewb": "Skewb",
"sk": "Skewb",
"skb": "Skewb",
"sq1": "Square-1",
"sq": "Square-1",
"444bf": "4x4x4 Blindfolded",
"4bld": "4x4x4 Blindfolded",
"4bf": "4x4x4 Blindfolded",
"555bf": "5x5x5 Blindfolded",
"5bld": "5x5x5 Blindfolded",
"5bf": "5x5x5 Blindfolded",
"333mbf": "3x3x3 Multiple Blindfolded",
"mbf": "3x3x3 Multiple Blindfolded",
"mbld": "3x3x3 Multiple Blindfolded",
"multi": "3x3x3 Multiple Blindfolded"}

passcode_db = {}
scramble_stack = {}

@server.add_route(path="/ping", method="GET")
async def ping():
  return web.json_response(data={"message": "pong"}, status=200)

@server.add_route(path="/ping/{channelId:\d+}", method="GET")
async def ping(request):
  channelId = request.match_info['channelId']
  channel = bot.get_channel(int(channelId))

  if channel is None:
    print(f"channel {channelId} not found")
    return web.json_response(data={"message": "channel not found"}, status=404)
  
  await channel.send('pong')
  return web.json_response(data={"message": "pong"}, status=200)

bot.run(TOKEN)
