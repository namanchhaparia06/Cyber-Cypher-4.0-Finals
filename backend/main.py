import asyncio
import json
import os
import random
import tempfile
from typing import Dict

import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from twilio.rest import Client
from websockets import connect


load_dotenv()

app = FastAPI()
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
speech_config = speechsdk.SpeechConfig(
    subscription=os.getenv("AZURE_SPEECH_KEY"), region=os.getenv("AZURE_SERVICE_REGION")
)
mongo_client = MongoClient(os.getenv("MONGO_URI"))
db = mongo_client.estate_agent

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

indian_languages = {
    "Assamese": {"Female": "as-IN-YashicaNeural", "Male": "as-IN-PriyomNeural"},
    "Bengali": {"Female": "bn-IN-TanishaaNeural", "Male": "bn-IN-BashkarNeural"},
    "Gujarati": {"Female": "gu-IN-DhwaniNeural", "Male": "gu-IN-NiranjanNeural"},
    "Hindi": {"Female": "hi-IN-AnanyaNeural", "Male": "hi-IN-AaravNeural"},
    "Kannada": {"Female": "kn-IN-SapnaNeural", "Male": "kn-IN-GaganNeural"},
    "Malayalam": {"Female": "ml-IN-SobhanaNeural", "Male": "ml-IN-MidhunNeural"},
    "Marathi": {"Female": "mr-IN-AarohiNeural", "Male": "mr-IN-ManoharNeural"},
    "Oriya": {"Female": "or-IN-SubhasiniNeural", "Male": "or-IN-SukantNeural"},
    "Punjabi": {"Female": "pa-IN-VaaniNeural", "Male": "pa-IN-OjasNeural"},
    "Tamil": {"Female": "ta-IN-PallaviNeural", "Male": "ta-IN-ValluvarNeural"},
    "Telugu": {"Female": "te-IN-ShrutiNeural", "Male": "te-IN-MohanNeural"},
    "Urdu": {"Female": "ur-IN-GulNeural", "Male": "ur-IN-SalmanNeural"},
    "English": {"Female": "en-IN-AashiNeural", "Male": "en-IN-AaravNeural"},
}


def tts(text, language, gender="Male"):
    speech_config.speech_synthesis_voice_name = indian_languages[language][gender]
    temp_file_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    audio_config = speechsdk.audio.AudioOutputConfig(filename=temp_file_path)
    speech_synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=audio_config
    )
    result = speech_synthesizer.speak_text_async(text).get()

    file_path = "output.wav"
    with open(file_path, "wb") as audio_file:
        audio_file.write(result.audio_data)
    return file_path


@app.post("/register")
async def register(name: str, no: str, gender: str):
    otp = random.randint(100000, 999999)
    db.users.insert_one(
        {
            "name": name,
            "no": no,
            "gender": gender,
            "otp": otp,
            "verified": False,
            "role": "user",
        }
    )
    twilio_client.messages.create(
        to=f"whatsapp:+91{no}",
        from_=os.getenv("TWILIO_PHONE_NUMBER"),
        content_sid="HX229f5a04fd0510ce1b071852155d3e75",
        content_variables='{"1":"' + str(otp) + '"}',
    )
    return {"status": "success"}


@app.post("/verify")
async def verify(no: str, otp: int):
    user = db.users.find_one({"no": no})
    if user["otp"] == otp:
        db.users.update_one({"no": no}, {"$set": {"verified": True}})
        return {"status": "success"}
    return {"status": "failure"}


@app.post("/tts")
async def tts_endpoint(text: str, language: str, gender: str):
    filename = tts(text, language, gender)
    return {"filename": filename}


class GeminiConnection:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = "gemini-2.0-flash-exp"
        self.uri = (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
            f"?key={self.api_key}"
        )
        self.ws = None
        self.config = None

    async def connect(self):
        """Initialize connection to Gemini"""
        self.ws = await connect(
            self.uri, extra_headers={"Content-Type": "application/json"}
        )

        if not self.config:
            raise ValueError("Configuration must be set before connecting")

        # Send initial setup message with configuration
        setup_message = {
            "setup": {
                "model": f"models/{self.model}",
                "generation_config": {
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "voice_config": {
                            "prebuilt_voice_config": {
                                "voice_name": self.config["voice"]
                            }
                        }
                    },
                },
                "system_instruction": {
                    "parts": [
                        {
                            "text": "You are a translation agent. Whatever the user says, JUST REPEAT IT IN HINDI. Do NOT add anything else. Preserve the meaning of the sentences the user says. Do NOT repeat what the user says in the same language."
                        }
                    ]
                },
            }
        }
        await self.ws.send(json.dumps(setup_message))

        # Wait for setup completion
        setup_response = await self.ws.recv()
        return setup_response

    def set_config(self, config):
        """Set configuration for the connection"""
        self.config = config

    async def send_audio(self, audio_data: str):
        """Send audio data to Gemini"""
        realtime_input_msg = {
            "realtime_input": {
                "media_chunks": [{"data": audio_data, "mime_type": "audio/pcm"}]
            }
        }
        await self.ws.send(json.dumps(realtime_input_msg))

    async def receive(self):
        """Receive message from Gemini"""
        return await self.ws.recv()

    async def close(self):
        """Close the connection"""
        if self.ws:
            await self.ws.close()

    async def send_image(self, image_data: str):
        """Send image data to Gemini"""
        image_message = {
            "realtime_input": {
                "media_chunks": [{"data": image_data, "mime_type": "image/jpeg"}]
            }
        }
        await self.ws.send(json.dumps(image_message))

    async def send_text(self, text: str):
        """Send text message to Gemini"""
        text_message = {
            "client_content": {
                "turns": [{"role": "user", "parts": [{"text": text}]}],
                "turn_complete": True,
            }
        }
        await self.ws.send(json.dumps(text_message))


# Store active connections
connections: Dict[str, GeminiConnection] = {}


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()

    try:
        # Create new Gemini connection for this client
        gemini = GeminiConnection()
        connections[client_id] = gemini

        # Wait for initial configuration
        config_data = await websocket.receive_json()
        if config_data.get("type") != "config":
            raise ValueError("First message must be configuration")

        # Set the configuration
        gemini.set_config(config_data.get("config", {}))

        # Initialize Gemini connection
        await gemini.connect()

        # Handle bidirectional communication
        async def receive_from_client():
            try:
                while True:
                    try:
                        # Check if connection is closed
                        if websocket.client_state.value == 3:  # WebSocket.CLOSED
                            print("WebSocket connection closed by client")
                            return

                        message = await websocket.receive()

                        # Check for close message
                        if message["type"] == "websocket.disconnect":
                            print("Received disconnect message")
                            await gemini.close()
                            return

                        message_content = json.loads(message["text"])
                        msg_type = message_content["type"]
                        if msg_type == "audio":
                            await gemini.send_audio(message_content["data"])
                        elif msg_type == "image":
                            await gemini.send_image(message_content["data"])
                        elif msg_type == "text":
                            await gemini.send_text(message_content["data"])
                        else:
                            print(f"Unknown message type: {msg_type}")
                    except json.JSONDecodeError as e:
                        print(f"JSON decode error: {e}")
                        continue
                    except KeyError as e:
                        print(f"Key error in message: {e}")
                        continue
                    except Exception as e:
                        print(f"Error processing client message: {str(e)}")
                        if "disconnect message" in str(e):
                            return
                        continue

            except Exception as e:
                print(f"Fatal error in receive_from_client: {str(e)}")
                return

        async def receive_from_gemini():
            try:
                while True:
                    if websocket.client_state.value == 3:  # WebSocket.CLOSED
                        print("WebSocket closed, stopping Gemini receiver")
                        return

                    msg = await gemini.receive()
                    response = json.loads(msg)

                    # Forward audio data to client
                    try:
                        parts = response["serverContent"]["modelTurn"]["parts"]
                        for p in parts:
                            # Check connection state before each send
                            if websocket.client_state.value == 3:
                                return

                            if "inlineData" in p:
                                audio_data = p["inlineData"]["data"]
                                print(audio_data)
                                await websocket.send_json(
                                    {"type": "audio", "data": audio_data}
                                )
                            elif "text" in p:
                                print(f"Received text: {p['text']}")
                                await websocket.send_json(
                                    {"type": "text", "data": p["text"]}
                                )
                    except KeyError:
                        pass

                    # Handle turn completion
                    try:
                        if response["serverContent"]["turnComplete"]:
                            await websocket.send_json(
                                {"type": "turn_complete", "data": True}
                            )
                    except KeyError:
                        pass
            except Exception as e:
                print(f"Error receiving from Gemini: {e}")

        # Run both receiving tasks concurrently
        async with asyncio.TaskGroup() as tg:
            tg.create_task(receive_from_client())
            tg.create_task(receive_from_gemini())

    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        # Cleanup
        if client_id in connections:
            await connections[client_id].close()
            del connections[client_id]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
