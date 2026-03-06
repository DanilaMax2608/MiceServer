# -*- coding: utf-8 -*-
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List, Dict, Optional
import uuid
import json
import random
import time
import asyncio

app = FastAPI()

lobbies: Dict[str, dict] = {}  

class LobbyCreateRequest(BaseModel):
    username: str

class LobbyJoinRequest(BaseModel):
    creator: str
    username: str

class StartGameRequest(BaseModel):
    lobby_id: str
    username: str
    seed: int = 0
    bonus_durations: Optional[Dict[str, float]] = None

def is_valid_username(username: str) -> bool:
    return username.startswith("@") and len(username) > 1

@app.post("/create_lobby")
async def create_lobby(request: LobbyCreateRequest):
    username = request.username
    if not is_valid_username(username):
        return {"error": "Invalid username"}
   
    if username in lobbies:
        return {"error": "A lobby with this name already exists."}
   
    lobby_id = str(uuid.uuid4())
    lobbies[username] = {
        "lobby_id": lobby_id,
        "creator": username,
        "players": [username],
        "status": "waiting",
        "max_players": 4,
        "scores": {username: 0},
        "seed": 0,
        "positions": {username: {"x": 0.0, "y": 0.0, "z": 0.0}},
        "rotations": {username: {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}},
        "items": {},
        "items_rotations": {},
        "mouse_traps": {},
        "mouse_traps_rotations": {},
        "ready_players": [],
        "messages": [],
        "bonus_durations": {
            "disable_control_others": 5.0,
            "slow_others": 5.0,
            "speed_up_others": 5.0,
            "invert_control_others": 5.0
        },
        "bonus_multipliers": {
            "slow_multiplier": 0.5,
            "speed_up_multiplier": 2.0
        },
        "created_at": time.time(),
        "timer_duration": 0,
        "timer_start_time": 0,
        "timer_is_running": False,
        "timer_task": None,
        "timer_sync_interval": 1.0,
        "clients": {},  
        "last_seen": {}  
    }
   
    print(f"Created lobby {lobby_id} for {username}")
    return {
        "lobby_id": lobby_id,
        "creator": username,
        "players": [username],
        "status": "waiting",
        "messages": []
    }

@app.post("/join_lobby")
async def join_lobby(request: LobbyJoinRequest):
    creator = request.creator
    username = request.username
   
    if not (is_valid_username(creator) and is_valid_username(username)):
        return {"error": "Invalid username"}
   
    if creator not in lobbies:
        return {"error": "Lobby not found"}
   
    lobby = lobbies[creator]
    if len(lobby["players"]) >= lobby["max_players"]:
        return {"error": "The lobby is full"}
   
    if username in lobby["players"]:
        return {"error": "You are already in the lobby"}
   
    lobby["players"].append(username)
    lobby["scores"][username] = 0
    lobby["positions"][username] = {"x": 0.0, "y": 0.0, "z": 0.0}
    lobby["rotations"][username] = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
   
    await notify_clients(lobby["lobby_id"], {
        "lobby_id": lobby["lobby_id"],
        "players": lobby["players"],
        "status": lobby["status"]
    })
   
    print(f"{username} joined lobby {lobby['lobby_id']}")
    return {
        "lobby_id": str(lobby["lobby_id"]),
        "creator": creator,
        "players": lobby["players"],
        "status": lobby["status"],
        "messages": lobby["messages"]
    }

@app.post("/start_game")
async def start_game(request: StartGameRequest):
    lobby_id = request.lobby_id
    username = request.username
    seed = request.seed
    bonus_durations = request.bonus_durations
   
    lobby = None
    creator = None
    for c, l in lobbies.items():
        if l["lobby_id"] == lobby_id:
            lobby = l
            creator = c
            break
   
    if not lobby:
        return {"error": "Lobby not found"}
   
    if username != lobby["creator"]:
        return {"error": "Only the creator can start the game"}
   
    if bonus_durations:
        lobby["bonus_durations"] = bonus_durations
        print(f"Received bonus durations from client: {bonus_durations}")
   
    lobby["status"] = "started"
    lobby["seed"] = seed
   
    print(f"Game started in lobby {lobby_id} with seed {seed} (creator: {username})")
   
    await notify_clients(lobby_id, {
        "lobby_id": lobby_id,
        "players": lobby["players"],
        "status": "started",
        "seed": seed,
        "items": lobby["items"],
        "items_rotations": lobby["items_rotations"],
        "mouse_traps": lobby["mouse_traps"],
        "mouse_traps_rotations": lobby["mouse_traps_rotations"]
    })
   
    return {"message": "Game has started", "seed": seed}

async def remove_player_from_lobby(lobby: dict, username: str, reason: str = "disconnect"):
    lobby_id = lobby["lobby_id"]
    if username == lobby["creator"]:
        for client in lobby["clients"].values():
            try:
                await client.send_json({"error": "Lobby closed by creator"})
            except:
                pass
        creator_key = None
        for c, l in lobbies.items():
            if l["lobby_id"] == lobby_id:
                creator_key = c
                break
        if creator_key:
            del lobbies[creator_key]
        print(f"Lobby {lobby_id} closed because creator {username} left ({reason})")
        return

    ws = lobby["clients"].get(username)
    if ws:
        try:
            await ws.close()
        except:
            pass
    del lobby["clients"][username]
    lobby["players"].remove(username)
    del lobby["scores"][username]
    del lobby["positions"][username]
    if username in lobby["rotations"]:
        del lobby["rotations"][username]
    if username in lobby["ready_players"]:
        lobby["ready_players"].remove(username)
    if username in lobby["last_seen"]:
        del lobby["last_seen"][username]

    await notify_clients(lobby_id, {
        "lobby_id": lobby_id,
        "players": lobby["players"],
        "status": lobby["status"]
    })
    print(f"{username} removed from lobby {lobby_id} due to {reason}")

async def check_inactive_players():
    while True:
        await asyncio.sleep(1) 
        current_time = time.time()
        for creator, lobby in list(lobbies.items()):
            lobby_id = lobby["lobby_id"]
            for username in list(lobby["players"]):
                last_seen = lobby["last_seen"].get(username, 0)
                if current_time - last_seen > 3.0:  
                    await remove_player_from_lobby(lobby, username, reason="inactivity")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(check_inactive_players())

@app.websocket("/ws/lobby")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_ip = websocket.client.host
    print(f"WebSocket client connected: {client_ip}")
   
    try:
        while True:
            try:
                data = await websocket.receive_text()
                print(f"Received message from {client_ip}: {data}")
                message = json.loads(data)
                action = message.get("action")
               
                if action == "create":
                    username = message.get("username")
                    if not is_valid_username(username):
                        await websocket.send_json({"error": "Invalid username"})
                        continue
                   
                    if username in lobbies:
                        await websocket.send_json({"error": "A lobby with this name already exists."})
                        continue
                   
                    lobby_id = str(uuid.uuid4())
                    lobbies[username] = {
                        "lobby_id": lobby_id,
                        "creator": username,
                        "players": [username],
                        "status": "waiting",
                        "max_players": 4,
                        "scores": {username: 0},
                        "seed": 0,
                        "positions": {username: {"x": 0.0, "y": 0.0, "z": 0.0}},
                        "rotations": {username: {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}},
                        "items": {},
                        "items_rotations": {},
                        "mouse_traps": {},
                        "mouse_traps_rotations": {},
                        "ready_players": [],
                        "messages": [],
                        "bonus_durations": {
                            "disable_control_others": 5.0,
                            "slow_others": 5.0,
                            "speed_up_others": 5.0,
                            "invert_control_others": 5.0
                        },
                        "bonus_multipliers": {
                            "slow_multiplier": 0.5,
                            "speed_up_multiplier": 2.0
                        },
                        "created_at": time.time(),
                        "timer_duration": 0,
                        "timer_start_time": 0,
                        "timer_is_running": False,
                        "timer_task": None,
                        "timer_sync_interval": 1.0,
                        "clients": {username: websocket},
                        "last_seen": {username: time.time()}
                    }
                   
                    await websocket.send_json({
                        "lobby_id": str(lobby_id),
                        "creator": username,
                        "players": [username],
                        "status": "waiting",
                        "messages": []
                    })
                    print(f"Created lobby {lobby_id} for {username}")
               
                elif action == "join":
                    creator = message.get("creator")
                    username = message.get("username")
                   
                    if not (is_valid_username(creator) and is_valid_username(username)):
                        await websocket.send_json({"error": "Invalid username"})
                        continue
                   
                    if creator not in lobbies:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
                   
                    lobby = lobbies[creator]
                    if len(lobby["players"]) >= lobby["max_players"]:
                        await websocket.send_json({"error": "The lobby is full"})
                        continue
                   
                    if username in lobby["players"]:
                        await websocket.send_json({"error": "You are already in the lobby"})
                        continue
                   
                    if lobby["status"] == "started":
                        await websocket.send_json({"error": "Game already started, cannot join"})
                        continue
                   
                    lobby["players"].append(username)
                    lobby["scores"][username] = 0
                    lobby["positions"][username] = {"x": 0.0, "y": 0.0, "z": 0.0}
                    lobby["rotations"][username] = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
                    lobby["clients"][username] = websocket
                    lobby["last_seen"][username] = time.time()
                   
                    await notify_clients(lobby["lobby_id"], {
                        "lobby_id": str(lobby["lobby_id"]),
                        "players": lobby["players"],
                        "status": "waiting"
                    })
                    print(f"{username} joined lobby {lobby['lobby_id']}")
                   
                    await websocket.send_json({
                        "lobby_id": str(lobby["lobby_id"]),
                        "creator": creator,
                        "players": lobby["players"],
                        "status": "waiting",
                        "messages": lobby["messages"]
                    })
               
                elif action == "start":
                    username = message.get("username")
                    lobby_id = message.get("lobby_id")
                    seed = message.get("seed", 0)
                   
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
                   
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
                   
                    if username != lobby["creator"]:
                        await websocket.send_json({"error": "Only the creator can start the game"})
                        continue
                   
                    lobby["status"] = "started"
                    lobby["seed"] = seed
                   
                    print(f"Game started in lobby {lobby_id} with seed {seed} (creator: {username})")
                   
                    await notify_clients(lobby_id, {
                        "lobby_id": str(lobby_id),
                        "players": lobby["players"],
                        "status": "started",
                        "seed": seed,
                        "items": lobby["items"],
                        "items_rotations": lobby["items_rotations"],
                        "mouse_traps": lobby["mouse_traps"],
                        "mouse_traps_rotations": lobby["mouse_traps_rotations"]
                    })
               
                elif action == "set_bonus_data":
                    username = message.get("username")
                    lobby_id = message.get("lobby_id")
                    bonus_durations = message.get("bonus_durations")
                    bonus_multipliers = message.get("bonus_multipliers")
                   
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
                   
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
                   
                    if username not in lobby["players"]:
                        await websocket.send_json({"error": "Player not in lobby"})
                        continue
                   
                    if bonus_durations:
                        lobby["bonus_durations"] = bonus_durations
                   
                    if bonus_multipliers:
                        lobby["bonus_multipliers"] = bonus_multipliers
                   
                    print(f"Updated bonus data for lobby {lobby_id}: durations={bonus_durations}, multipliers={bonus_multipliers}")
                    await websocket.send_json({"message": "Bonus data updated"})
               
                elif action == "leave":
                    lobby_id = message.get("lobby_id")
                    username = message.get("username")
                   
                    lobby = None
                    creator_key = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            creator_key = c
                            break
                   
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
                   
                    if username == lobby["creator"]:
                        if lobby["timer_task"] is not None:
                            lobby["timer_task"].cancel()
                        for client in lobby["clients"].values():
                            if client != websocket:
                                try:
                                    await client.send_json({"error": "Lobby closed by creator"})
                                except Exception as e:
                                    print(f"Error notifying client: {e}")
                        del lobbies[creator_key]
                        print(f"Lobby {lobby_id} deleted by creator {username}")
                        await websocket.send_json({"message": "Lobby closed"})
                    else:
                        if username in lobby["players"]:
                            del lobby["clients"][username]
                            lobby["players"].remove(username)
                            del lobby["scores"][username]
                            del lobby["positions"][username]
                            if username in lobby["rotations"]:
                                del lobby["rotations"][username]
                            if username in lobby["ready_players"]:
                                lobby["ready_players"].remove(username)
                            if username in lobby["last_seen"]:
                                del lobby["last_seen"][username]
                            await notify_clients(lobby_id, {
                                "lobby_id": lobby_id,
                                "players": lobby["players"],
                                "status": lobby["status"]
                            })
                            print(f"{username} left lobby {lobby_id}")
                            await websocket.send_json({"message": "Left lobby"})
               
                elif action == "ready":
                    username = message.get("username")
                    lobby_id = message.get("lobby_id")
                   
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
                   
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
                   
                    if username not in lobby["players"]:
                        await websocket.send_json({"error": "Player not in lobby"})
                        continue
                   
                    if username not in lobby["ready_players"]:
                        lobby["ready_players"].append(username)
                        print(f"{username} signaled ready in lobby {lobby_id}. Ready players: {len(lobby['ready_players'])}/{len(lobby['players'])}")
                       
                        if len(lobby["ready_players"]) == len(lobby["players"]):
                            print(f"All players ready in lobby {lobby_id}, broadcasting start_game")
                            await notify_clients(lobby_id, {
                                "action": "start_game",
                                "lobby_id": lobby_id
                            })
               
                elif action == "update_position":
                    lobby_id = message.get("lobby_id")
                    username = message.get("username")
                    x = message.get("x", 0.0)
                    y = message.get("y", 0.0)
                    z = message.get("z", 0.0)
                    rot_x = message.get("rot_x")
                    rot_y = message.get("rot_y")
                    rot_z = message.get("rot_z")
                    rot_w = message.get("rot_w")
                   
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
                   
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
                   
                    if username not in lobby["players"]:
                        await websocket.send_json({"error": "Player not in lobby"})
                        continue
                   
                    lobby["positions"][username] = {"x": x, "y": y, "z": z}
                   
                    if rot_x is not None and rot_y is not None and rot_z is not None and rot_w is not None:
                        lobby["rotations"][username] = {"x": rot_x, "y": rot_y, "z": rot_z, "w": rot_w}
                        print(f"Updated position and rotation for {username} in lobby {lobby_id}: pos=({x},{y},{z}), rot=({rot_x},{rot_y},{rot_z},{rot_w})")
                    else:
                        print(f"Updated position for {username} in lobby {lobby_id}: ({x},{y},{z})")
                   
                    update_message = {
                        "action": "update_position",
                        "lobby_id": lobby_id,
                        "username": username,
                        "x": x,
                        "y": y,
                        "z": z
                    }
                   
                    if rot_x is not None and rot_y is not None and rot_z is not None and rot_w is not None:
                        update_message.update({
                            "rot_x": rot_x,
                            "rot_y": rot_y,
                            "rot_z": rot_z,
                            "rot_w": rot_w
                        })
                   
                    await notify_clients(lobby_id, update_message)
               
                elif action == "collect_item":
                    lobby_id = message.get("lobby_id")
                    username = message.get("username")
                    item_id = message.get("item_id")
                   
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
                   
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
                   
                    if username not in lobby["players"]:
                        await websocket.send_json({"error": "Player not in lobby"})
                        continue
                   
                    if item_id not in lobby["items"]:
                        await websocket.send_json({"error": "Item not found"})
                        continue
                   
                    if lobby["items"][item_id]["collected"]:
                        await websocket.send_json({"error": "Item already collected"})
                        continue
                   
                    lobby["items"][item_id]["collected"] = True
                    lobby["scores"][username] = lobby["scores"].get(username, 0) + 1
                    print(f"Item {item_id} collected by {username} in lobby {lobby_id}, new score: {lobby['scores'][username]}")
                   
                    await notify_clients(lobby_id, {
                        "action": "item_collected",
                        "lobby_id": lobby_id,
                        "item_id": item_id,
                        "username": username,
                        "scores": lobby["scores"]
                    })
               
                elif action == "collect_bonus":
                    lobby_id = message.get("lobby_id")
                    username = message.get("username")
                    item_id = message.get("item_id")
                    bonus_type = message.get("bonus_type")
   
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
   
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
   
                    if username not in lobby["players"]:
                        await websocket.send_json({"error": "Player not in lobby"})
                        continue
   
                    if item_id not in lobby["items"]:
                        await websocket.send_json({"error": "Item not found"})
                        continue
   
                    if not lobby["items"][item_id]["is_bonus"]:
                        await websocket.send_json({"error": "Item is not a bonus item"})
                        continue
   
                    if lobby["items"][item_id]["collected"]:
                        await websocket.send_json({"error": "Bonus item already collected"})
                        continue
   
                    lobby["items"][item_id]["collected"] = True
                    lobby["scores"][username] = lobby["scores"].get(username, 0) + 1
                    print(f"Bonus item {item_id} collected by {username} in lobby {lobby_id}, bonus_type: {bonus_type}, new score: {lobby['scores'][username]}")
   
                    await notify_clients(lobby_id, {
                        "action": "item_collected",
                        "lobby_id": lobby_id,
                        "item_id": item_id,
                        "username": username,
                        "bonus_type": bonus_type,
                        "scores": lobby["scores"]
                    })
   
                    bonus_durations = lobby.get("bonus_durations", {})
                    bonus_multipliers = lobby.get("bonus_multipliers", {})
   
                    if bonus_type == "disable_control_others":
                        duration = bonus_durations.get("disable_control_others")
                        if duration is None:
                            duration = 5.0
                            print(f"Warning: disable_control_others duration not found, using default: {duration}")
                       
                        for player in lobby["players"]:
                            if player != username:
                                await notify_clients(lobby_id, {
                                    "action": "apply_effect",
                                    "effect_type": "disable_control",
                                    "target_username": player,
                                    "duration": duration
                                })
                   
                    elif bonus_type == "slow_others":
                        duration = bonus_durations.get("slow_others")
                        if duration is None:
                            duration = 5.0
                            print(f"Warning: slow_others duration not found, using default: {duration}")
                       
                        speed_multiplier = bonus_multipliers.get("slow_multiplier")
                        if speed_multiplier is None:
                            speed_multiplier = 0.5
                            print(f"Warning: slow_multiplier not found, using default: {speed_multiplier}")
                       
                        for player in lobby["players"]:
                            if player != username:
                                await notify_clients(lobby_id, {
                                    "action": "apply_effect",
                                    "effect_type": "slow_others",
                                    "target_username": player,
                                    "duration": duration,
                                    "speed_multiplier": speed_multiplier
                                })
                   
                    elif bonus_type == "speed_up_others":
                        duration = bonus_durations.get("speed_up_others")
                        if duration is None:
                            duration = 5.0
                            print(f"Warning: speed_up_others duration not found, using default: {duration}")
                       
                        speed_multiplier = bonus_multipliers.get("speed_up_multiplier")
                        if speed_multiplier is None:
                            speed_multiplier = 2.0
                            print(f"Warning: speed_up_multiplier not found, using default: {speed_multiplier}")
                       
                        for player in lobby["players"]:
                            if player != username:
                                await notify_clients(lobby_id, {
                                    "action": "apply_effect",
                                    "effect_type": "speed_up_others",
                                    "target_username": player,
                                    "duration": duration,
                                    "speed_multiplier": speed_multiplier
                                })
                   
                    elif bonus_type == "invert_control_others":
                        duration = bonus_durations.get("invert_control_others")
                        if duration is None:
                            duration = 5.0
                            print(f"Warning: invert_control_others duration not found, using default: {duration}")
                       
                        for player in lobby["players"]:
                            if player != username:
                                await notify_clients(lobby_id, {
                                    "action": "apply_effect",
                                    "effect_type": "invert_control",
                                    "target_username": player,
                                    "duration": duration
                                })
               
                elif action == "collect_trap":
                    lobby_id = message.get("lobby_id")
                    username = message.get("username")
                    trap_id = message.get("trap_id")
                    loss_percentage = message.get("loss_percentage", 0)
                   
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
                   
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
                   
                    if username not in lobby["players"]:
                        await websocket.send_json({"error": "Player not in lobby"})
                        continue
                   
                    if trap_id not in lobby["mouse_traps"]:
                        await websocket.send_json({"error": "Mouse trap not found"})
                        continue
                   
                    if lobby["mouse_traps"][trap_id]["triggered"]:
                        await websocket.send_json({"error": "Mouse trap already triggered"})
                        continue
                   
                    lobby["mouse_traps"][trap_id]["triggered"] = True
                   
                    current_score = lobby["scores"].get(username, 0)
                    items_to_remove = int(current_score * loss_percentage / 100)
                    lobby["scores"][username] = current_score - items_to_remove
                   
                    print(f"Mouse trap {trap_id} triggered by {username} in lobby {lobby_id}, loss: {loss_percentage}% ({items_to_remove} items), new score: {lobby['scores'][username]}")
                   
                    await notify_clients(lobby_id, {
                        "action": "trap_triggered",
                        "lobby_id": lobby_id,
                        "trap_id": trap_id,
                        "username": username,
                        "loss_percentage": loss_percentage,
                        "scores": lobby["scores"]
                    })
               
                elif action == "register_items":
                    lobby_id = message.get("lobby_id")
                    items = message.get("items", [])
      
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
      
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
      
                    lobby["items"] = {}
                    lobby["items_rotations"] = {}
                    for item in items:
                        item_id = item.get("item_id")
                        if item_id:
                            lobby["items"][item_id] = {
                                "collected": False,
                                "position": item.get("position", {"x": 0, "y": 0, "z": 0}),
                                "is_bonus": item.get("is_bonus", False),
                                "bonus_type": item.get("bonus_type", "")
                            }
                            rotation = item.get("rotation")
                            if rotation:
                                lobby["items_rotations"][item_id] = rotation
                            else:
                                lobby["items_rotations"][item_id] = {"x": 0, "y": 0, "z": 0, "w": 1}
      
                    await notify_clients(lobby_id, {
                        "action": "items_registered",
                        "lobby_id": lobby_id,
                        "items_count": len(lobby["items"])
                    })
      
                    print(f"Registered {len(lobby['items'])} items with rotations in lobby {lobby_id}")
               
                elif action == "register_mouse_traps":
                    lobby_id = message.get("lobby_id")
                    mouse_traps = message.get("mouse_traps", [])
      
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
      
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
      
                    lobby["mouse_traps"] = {}
                    lobby["mouse_traps_rotations"] = {}
                    for trap in mouse_traps:
                        trap_id = trap.get("trap_id")
                        if trap_id:
                            lobby["mouse_traps"][trap_id] = {
                                "triggered": False,
                                "position": trap.get("position", {"x": 0, "y": 0, "z": 0})
                            }
                            rotation = trap.get("rotation")
                            if rotation:
                                lobby["mouse_traps_rotations"][trap_id] = rotation
                            else:
                                lobby["mouse_traps_rotations"][trap_id] = {"x": 0, "y": 0, "z": 0, "w": 1}
      
                    await notify_clients(lobby_id, {
                        "action": "mouse_traps_registered",
                        "lobby_id": lobby_id,
                        "mouse_traps_count": len(lobby["mouse_traps"])
                    })
      
                    print(f"Registered {len(lobby['mouse_traps'])} mouse traps with rotations in lobby {lobby_id}")
               
                elif action == "send_message":
                    lobby_id = message.get("lobby_id")
                    username = message.get("username")
                    chat_message = message.get("message")
                   
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
                   
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
                   
                    if username not in lobby["players"]:
                        await websocket.send_json({"error": "Player not in lobby"})
                        continue
                   
                    if not chat_message or len(chat_message.strip()) == 0:
                        await websocket.send_json({"error": "Message cannot be empty"})
                        continue
                       
                    lobby["messages"].append({"username": username, "message": chat_message})
                    print(f"Message from {username} in lobby {lobby_id}: {chat_message}")
                   
                    await notify_clients(lobby_id, {
                        "action": "chat_message",
                        "lobby_id": lobby_id,
                        "username": username,
                        "message": chat_message
                    })
               
                elif action == "get_lobbies":
                    available_lobbies = [
                        {
                            "lobby_id": lobby["lobby_id"],
                            "creator": creator,
                            "current_players": len(lobby["players"]),
                            "max_players": lobby["max_players"]
                        }
                        for creator, lobby in lobbies.items()
                        if lobby["status"] == "waiting"
                    ]
                    await websocket.send_json({
                        "action": "lobbies_list",
                        "lobbies": available_lobbies
                    })
                    print(f"Sent {len(available_lobbies)} available lobbies to client {client_ip}")
               
                elif action == "start_server_timer":
                    lobby_id = message.get("lobby_id")
                    username = message.get("username")
                    duration = message.get("duration", 0)
                   
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
                   
                    if not lobby:
                        await websocket.send_json({"error": "Lobby not found"})
                        continue
                   
                    if username != lobby["creator"]:
                        await websocket.send_json({"error": "Only the creator can start the timer"})
                        continue
                   
                    await start_server_timer(lobby_id, duration)
                    print(f"Server timer started in lobby {lobby_id} with duration {duration} seconds")
               
                elif action == "ping":
                    username = message.get("username")
                    lobby_id = message.get("lobby_id")
                    if not lobby_id:
                        await websocket.send_json({"action": "pong"})
                        continue
                   
                    lobby = None
                    for c, l in lobbies.items():
                        if l["lobby_id"] == lobby_id:
                            lobby = l
                            break
                   
                    if lobby and username in lobby["players"]:
                        lobby["last_seen"][username] = time.time()
                        await websocket.send_json({"action": "pong"})
                        print(f"Ping from {username} in lobby {lobby_id}, last_seen updated")
                    else:
                        await websocket.send_json({"action": "pong"})
           
            except WebSocketDisconnect:
                await handle_disconnect(websocket)
                break
   
    except WebSocketDisconnect:
        await handle_disconnect(websocket)

async def start_server_timer(lobby_id: str, duration: float):
    lobby = None
    for c, l in lobbies.items():
        if l["lobby_id"] == lobby_id:
            lobby = l
            break
   
    if not lobby:
        print(f"Lobby {lobby_id} not found for starting timer")
        return
   
    if lobby["timer_task"] is not None:
        lobby["timer_task"].cancel()
   
    lobby["timer_duration"] = duration
    lobby["timer_start_time"] = time.time()
    lobby["timer_is_running"] = True
   
    lobby["timer_task"] = asyncio.create_task(timer_sync_task(lobby_id))
   
    await notify_clients(lobby_id, {
        "action": "timer_started",
        "duration": duration,
        "start_time": lobby["timer_start_time"]
    })
   
    print(f"Timer started for lobby {lobby_id} with duration {duration}")

async def timer_sync_task(lobby_id: str):
    lobby = None
    for c, l in lobbies.items():
        if l["lobby_id"] == lobby_id:
            lobby = l
            break
   
    if not lobby:
        return
   
    sync_interval = lobby.get("timer_sync_interval", 1.0)
   
    try:
        while lobby["timer_is_running"]:
            elapsed = time.time() - lobby["timer_start_time"]
            remaining = max(0, lobby["timer_duration"] - elapsed)
           
            await notify_clients(lobby_id, {
                "action": "timer_sync",
                "remaining": remaining,
                "elapsed": elapsed,
                "duration": lobby["timer_duration"],
                "start_time": lobby["timer_start_time"],
                "is_running": remaining > 0
            })
           
            if remaining <= 0:
                print(f"Timer finished for lobby {lobby_id}")
                await finish_server_timer(lobby_id)
                break
           
            await asyncio.sleep(sync_interval)
           
    except asyncio.CancelledError:
        print(f"Timer sync task cancelled for lobby {lobby_id}")
        raise
    except Exception as e:
        print(f"Error in timer sync task for lobby {lobby_id}: {e}")
    finally:
        lobby["timer_task"] = None
        lobby["timer_is_running"] = False

async def finish_server_timer(lobby_id: str):
    lobby = None
    for c, l in lobbies.items():
        if l["lobby_id"] == lobby_id:
            lobby = l
            break
   
    if not lobby:
        return
   
    lobby["timer_is_running"] = False
   
    await notify_clients(lobby_id, {
        "action": "timer_finished",
        "duration": lobby["timer_duration"]
    })
   
    print(f"Timer finished for lobby {lobby_id}")

async def handle_disconnect(websocket: WebSocket):
    client_ip = websocket.client.host
    for creator, lobby in list(lobbies.items()):
        for username, ws in list(lobby["clients"].items()):
            if ws == websocket:
                await remove_player_from_lobby(lobby, username, reason="disconnect")
                print(f"WebSocket client disconnected: {client_ip} (username: {username})")
                return
    print(f"WebSocket client disconnected: {client_ip} (not in any lobby)")

async def notify_clients(lobby_id: str, message: dict):
    lobby = None
    for c, l in lobbies.items():
        if l["lobby_id"] == lobby_id:
            lobby = l
            break
    if not lobby:
        return
    for client in list(lobby["clients"].values()):
        try:
            await client.send_json(message)
        except Exception as e:
            print(f"Error notifying client: {e}")