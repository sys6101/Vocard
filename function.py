import discord
import json
import aiohttp
import os

from discord.ext import commands
from dotenv import load_dotenv
from random import choice
from datetime import datetime
from time import strptime
from io import BytesIO
from pymongo import MongoClient
from typing import Optional

root_dir = os.path.dirname(os.path.abspath(__file__))

if not os.path.exists(os.path.join(root_dir, "settings.json")):
    raise Exception("Settings file not set!")

#-------------- API Clients --------------
load_dotenv() #Load .env settings
MONGODB_NAME = os.getenv('MONGODB_NAME')
MONGODB_URL = os.getenv('MONGODB_URL')
if not (MONGODB_NAME and MONGODB_URL):
    raise Exception("MONGODB_NAME and MONGODB_URL can't not be empty in .env")

try:
    mongodb = MongoClient(host=MONGODB_URL, serverSelectionTimeoutMS=5000)
    mongodb.server_info()
    print("Successfully connected to MongoDB!")
except Exception as e:
    raise Exception("Not able to connect MongoDB! Reason:", e)

collection = mongodb[MONGODB_NAME]['Settings']
Playlist = mongodb[MONGODB_NAME]['Playlist']
youtube_api_key = os.getenv('YOUTUBE_API_KEY')

#--------------- Cache Var ---------------
invite_link = "https://discord.gg/wRCgB7vBQv" #Template of invite link
embed_color = None

try:
    report_channel_id = int(os.getenv("BUG_REPORT_CHANNEL_ID"))
except:
    report_channel_id = 0

emoji_source_raw = {} #Stores all source emoji for track
error_log = {} #Stores error that not a Voicelink Exception
bot_access_user = [] #Stores bot access user id
langs = {} #Stores all the languages in ./langs
guild_settings = {} #Cache guild language
local_langs = {} #Stores all the localization languages in ./local_langs 
playlist_name = {} #Cache the user's playlist name
bot_prefix = "?" #The default bot prefix
max_queue = 1000 #The default maximum number of tracks in the queue
cooldowns_settings = {} #Stores command cooldown settings
aliases_settings = {} #Stores command aliases settings
controller_settings = {} #Stores the layout of music controller

#----------------- Nodes -----------------
nodes = {}

#-------------- Vocard Functions --------------
def get_settings(guild_id:int):
    settings = guild_settings.get(guild_id, None)
    if not settings:
        settings = collection.find_one({"_id":guild_id})
        if not settings:
            collection.insert_one({"_id":guild_id})
            settings = {}
        guild_settings[guild_id] = settings
    return settings

def update_settings(guild_id:int, data: dict, mode="Set"):
    settings = get_settings(guild_id)
    if mode == "Set":
        for key, value in data.items():
            if settings.get(key) != value:
                guild_settings[guild_id][key] = value
        collection.update_one({"_id":guild_id}, {"$set":data})
    elif mode == "Delete":
        for key, value in data.items():
            if settings.get(key) != value:
                del guild_settings[guild_id][key]
        collection.update_one({"_id":guild_id}, {"$unset":data})
    return

def langs_setup():
    for language in os.listdir(os.path.join(root_dir, "langs")):
        if language.endswith('.json'):
            with open(os.path.join(root_dir, "langs", language), encoding="utf8") as json_file:
                lang = json.load(json_file)

            langs[language[:-5]] = lang
    
    for language in os.listdir(os.path.join(root_dir, "local_langs")):
        if language.endswith('.json'):
            with open(os.path.join(root_dir, "local_langs", language), encoding="utf8") as json_file:
                lang = json.load(json_file)
        
            local_langs[language[:-5]] = lang
    return

def settings_setup():
    with open(os.path.join(root_dir, "settings.json"), encoding="utf8") as json_file:
        rawSettings = json.load(json_file)

    global nodes, max_queue, bot_prefix, embed_color, bot_access_user, emoji_source_raw, cooldowns_settings, aliases_settings, controller_settings
    nodes = rawSettings.get("nodes", {})
    if (new_max_queue := rawSettings.get("default_max_queue", max_queue)):
        max_queue = new_max_queue
    bot_prefix = rawSettings.get("prefix", "")
    embed_color = int(rawSettings.get("embed_color", "0xb3b3b3"), 16)
    bot_access_user = rawSettings.get("bot_access_user", [])
    emoji_source_raw = rawSettings.get("emoji_source_raw", {})
    cooldowns_settings = rawSettings.get("cooldowns", {})
    aliases_settings = rawSettings.get("aliases", {})
    controller_settings = rawSettings.get("controller", [["back", "resume", "skip", {"stop": "red"}, "add"], ["tracks"]])

def get_lang(guild_id:int, key:str):
    lang = get_settings(guild_id).get("lang", "EN")
    return langs.get(lang, langs["EN"])[key]
    
async def requests_api(url: str):
    async with aiohttp.ClientSession() as session:
        resp = await session.get(url)
        if resp.status != 200:
            return False

        return await resp.json(encoding="utf-8")

async def create_account(ctx: commands.Context):
    if not ctx.author:
        return 
    from views import CreateView
    view = CreateView()
    embed=discord.Embed(title="Do you want to create an account on Vocard?", color=embed_color)
    embed.description = f"> Plan: `Default` | `5` Playlist | `500` tracks in each playlist."
    embed.add_field(name="Terms of Service:", value="‌    ➥ We assure you that all your data on Vocard will not be disclosed to any third party\n"
                                                    "‌    ➥ We will not perform any data analysis on your data\n"
                                                    "‌    ➥ You have the right to immediately stop the services we offer to you\n"
                                                    "‌    ➥ Please do not abuse our services, such as affecting other users\n", inline=False)
    message = await ctx.reply(embed=embed, view=view, ephemeral=True)
    view.response = message
    await view.wait()
    if view.value:
        try:
            Playlist.insert_one({'_id':ctx.author.id, 'playlist': {'200':{'tracks':[],'perms':{ 'read': [], 'write':[], 'remove': []},'name':'Favourite', 'type':'playlist' }},'inbox':[] })
        except:
            pass
            
async def get_playlist(userid:int, dType:str=None, dId:str=None):
    user = Playlist.find_one({"_id":userid}, {"_id": 0})
    if not user:
        return None
    if dType:
        if dId and dType == "playlist":
            return user[dType][dId] if dId in user[dType] else None
        return user[dType]
    return user

async def update_playlist(userid:int, data:dict=None, push=False, pull=False, mode=True):
    if mode is True:
        if push:
            return Playlist.update_one({"_id":userid}, {"$push": data})
        Playlist.update_one({"_id":userid}, {"$set": data})
    else:
        if pull:
            return Playlist.update_one({"_id":userid}, {"$pull": data})
        Playlist.update_one({"_id":userid}, {"$unset": data})
    return

async def update_inbox(userid:int, data:dict):
    return Playlist.update_one({"_id":userid}, {"$push":{'inbox':data}})

async def checkroles(userid:int):
    rank, max_p, max_t = 'Normal', 5, 500

    return rank, max_p, max_t

async def similar_track(player) -> bool:
    trackids = [ track.identifier for track in player.queue.history(incTrack=True) if track.source == 'youtube' ]
    randomTrack = choice(player.queue.history(incTrack=True)[-10:])
    tracks = []

    if randomTrack.spotify:
        tracks = await player.spotifyRelatedTrack(seed_artists=randomTrack.artistId[0], seed_tracks=randomTrack.track_id)
    else:
        if randomTrack.source != 'youtube':
            return False

        if not youtube_api_key:
            return False
        
        request_url = "https://youtube.googleapis.com/youtube/v3/search?part={part}&relatedToVideoId={videoId}&type={type}&videoCategoryId={videoCategoryId}&key={key}".format(
            part="snippet",
            videoId=randomTrack.identifier,
            type="video",
            videoCategoryId="10",
            key=youtube_api_key
        )

        try:
            data = await requests_api(request_url)
            if not data:
                return False

            for item in data['items']:
                if 'snippet' not in item:
                    continue
                if item['id']['videoId'] not in trackids:
                    tracks = await player.get_tracks(f"https://www.youtube.com/watch?v={item['id']['videoId']}", requester=player._bot.user)
                    break
        except:
            return False

    if tracks:
        await player.add_track(tracks)
        return True

    return False

async def connect_channel(ctx: commands.Context, channel: discord.VoiceChannel = None):
    import voicelink
    try:
        channel = channel or ctx.author.voice.channel
    except:
        raise voicelink.VoicelinkException(get_lang(ctx.guild.id, 'noChannel'))

    check = channel.permissions_for(ctx.guild.me)
    if check.connect == False or check.speak == False:
        raise voicelink.VoicelinkException(
            get_lang(ctx.guild.id, 'noPermission'))

    player: voicelink.Player = await channel.connect(cls=voicelink.Player(ctx.bot, channel, ctx))
    await player.send_ws({"op": "createPlayer", "members_id": [member.id for member in channel.members]})
    return player

def time(millis:int) -> str:
    seconds=(millis/1000)%60
    minutes=(millis/(1000*60))%60
    hours=(millis/(1000*60*60))%24
    if hours > 1:
        return "%02d:%02d:%02d" % (hours, minutes, seconds)
    else:
        return "%02d:%02d" % (minutes, seconds)

def formatTime(number:str) -> Optional[int]:
    try:
        try:
            num = strptime(number, '%M:%S')
        except ValueError:
            try:
                num = strptime(number, '%S')
            except ValueError:
                num = strptime(number, '%H:%M:%S')
    except:
        return None
    
    return (int(num.tm_hour) * 3600 + int(num.tm_min) * 60 + int(num.tm_sec)) * 1000

def emoji_source(emoji:str):
    return emoji_source_raw.get(emoji.lower(), "🔗")

def gen_report() -> Optional[discord.File]:
    if error_log:
        errorText = ""
        for guild_id, error in error_log.items():
            errorText += f"Guild ID: {guild_id}\n" + "-" * 30 + "\n"
            for index, (key, value) in enumerate(error.items() , start=1):
                errorText += f"Error No: {index}, Time: {datetime.fromtimestamp(key)}\n" + value + "-" * 30 + "\n\n"

        buffer = BytesIO(errorText.encode('utf-8'))
        return discord.File(buffer, filename='report.txt')

    return None

def cooldown_check(ctx: commands.Context) -> Optional[commands.Cooldown]:
    if ctx.author.id in bot_access_user:
        return None
    cooldown = cooldowns_settings.get(f"{ctx.command.parent.qualified_name} {ctx.command.name}" if ctx.command.parent else ctx.command.name)
    if not cooldown:
        return None
    return commands.Cooldown(cooldown[0], cooldown[1])

def get_aliases(name: str) -> list:
    return aliases_settings.get(name, [])
