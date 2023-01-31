from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, Depends
from typing import List
from pydantic import BaseModel, Field
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from starlette.staticfiles import StaticFiles
import datetime

import uvicorn

from src.elastic.elastic import ElasticObject
from urllib import parse

import requests
import pprint
elastic_connector = ElasticObject("localhost:9200")    
app = FastAPI()

app.mount("/assets", app=StaticFiles(directory="assets"), name='assets')

templates = Jinja2Templates(directory='./templates')


@app.on_event('startup')
def make_history_index():
    if not elastic_connector.client.indices.exists(index='chat-history'):
        elastic_connector.create_index("chat-history", setting_path="./src/elastic/history_settings.json")
            

async def load_chat():
    try:
        body = {
            "size": 1000,
            "query": {
                "match_all": {}
            },
            "sort": [
                {
                    "date": {
                        "order": "asc"
                    }
                }
            ]
        }
        resp = elastic_connector.client.search(index="chat-history", body=body)
        if resp['hits']['hits']:
            for res in resp['hits']['hits']:
                await manager.broadcast(res['_source'])
    except:
        pass
    


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    return templates.TemplateResponse('home.html', {"request": request})

@app.get("/chat", response_class=HTMLResponse)
def read_chat(request: Request):
    return templates.TemplateResponse('index.html', {"request": request})

@app.get("/api/current_user")
def get_user(request: Request):
    return parse.unquote(request.cookies.get("X-Authorization"))

class RegisterValidator(BaseModel):
    username: str
    
    class Config:
        orm_mode = True
        
@app.post("/api/register")
def register_user(user: RegisterValidator, response: Response):
    response.set_cookie(key="X-Authorization", value=parse.quote(user.username), httponly=True)
    
    

async def summary_retrieve(summary):
    
    _, outputs = elastic_connector.search(index_name="blogs", question=summary['answer'], topk=5)
    
    return outputs

    
class SocketManager:
    def __init__(self):
        self.active_connections: List[(WebSocket, str)] = []
        self.update_time = datetime.datetime.now()

    async def connect(self, websocket: WebSocket, user: str):
        await websocket.accept()
        self.active_connections.append((websocket, user))

    def disconnect(self, websocket: WebSocket, user: str):
        self.active_connections.remove((websocket, user))

    async def broadcast(self, data: dict):
        for connection in self.active_connections:
            await connection[0].send_json(data)
            
    def check_recommend(self):
        now_time = datetime.datetime.now()
        if ((now_time - self.update_time).seconds / 60) > 5:
            self.update_time = now_time
            return True
        return False
        
        

manager = SocketManager()

@app.websocket("/api/chat")
async def chat(websocket: WebSocket):
    sender = websocket.cookies.get("X-Authorization")
    sender = parse.unquote(sender)
    if sender:
        await manager.connect(websocket, sender)
        response = {
            "location": "chat",
            "sender": sender,
            "message": "접속하셨습니다."
        }
        messages = ""
        await manager.broadcast(response)
        await load_chat()
        try:
            while True:
                data = await websocket.receive_json()
                messages += data['message']
                await manager.broadcast(data)
                
                if len(messages) >= 100 or (manager.check_recommend() and len(messages) > 70):
                    summary_output = requests.post("http://localhost:8502", json={"text": messages}).json()
                    outputs = await summary_retrieve(summary_output)
                    
                    current_time = (datetime.datetime.now() - datetime.timedelta(hours=3)).strftime('%Y/%m/%d %H:%M:%S')
                    outputs['date'] = current_time
                    elastic_connector.client.index(index='chat-history', doc_type='_doc', body=outputs)
                    messages = ""
                    await manager.broadcast(outputs)
                        
                
        except WebSocketDisconnect:
            manager.disconnect(websocket, sender)
            response['message'] = "나가셨습니다."
            await manager.broadcast(response)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=30001)