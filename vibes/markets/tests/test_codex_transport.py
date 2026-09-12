import asyncio
import sys

import pytest

from governor.app_client import AppError
from governor.codex_transport import CodexTransport


@pytest.fixture
def fake_server(tmp_path):
    script = tmp_path / "server.py"
    script.write_text("""import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if 'method' not in m or 'id' not in m: continue
 method=m['method']
 if method=='disconnect': break
 if method=='fail':
  print(json.dumps({'id':m['id'],'error':{'message':'refused'}}),flush=True);continue
 result={'thread':{'id':'thread-one'}} if method=='thread/start' else {}
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
 if method=='turn/start':
  for event in [
   {'method':'item/agentMessage/delta','params':{'delta':m['params']['input'][0]['text']}},
   {'id':100,'method':'item/commandExecution/requestApproval','params':{'command':'pwd'}},
   {'method':'turn/completed','params':{'turn':{'id':'turn-one','status':'completed'}}}]:
   print(json.dumps(event),flush=True)
""")
    return [sys.executable, str(script)]


async def test_persistent_turns_requests_and_disconnect(fake_server, tmp_path):
    events = []
    complete = asyncio.Event()

    async def receive(event):
        events.append(event)
        if "id" in event:
            await transport.reply(event["id"], {"decision": "decline"})
        if event["method"] == "turn/completed":
            complete.set()

    transport = CodexTransport(fake_server, tmp_path, receive)
    await transport.start()
    thread = await transport.request("thread/start")
    for text in ["first prompt", "follow-up prompt"]:
        complete.clear()
        await transport.request(
            "turn/start",
            {"threadId": thread["thread"]["id"], "input": [{"type": "text", "text": text}]},
        )
        await asyncio.wait_for(complete.wait(), 2)
    assert [e["params"]["delta"] for e in events if e["method"].endswith("/delta")] == [
        "first prompt",
        "follow-up prompt",
    ]
    with pytest.raises(AppError, match="refused"):
        await transport.request("fail")
    with pytest.raises(AppError, match="disconnected"):
        await transport.request("disconnect")
    assert not transport.pending
    await transport.close()
