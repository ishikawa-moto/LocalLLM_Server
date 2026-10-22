"""Public protocol frames only; no model, keys, production config or service."""
import unittest
from unittest.mock import patch
import slack_gateway as s

def frame(delta):
 import json
 return ("data: "+json.dumps({"choices":[{"delta":delta}]})+"\n").encode()

def invoke(frames,lease=None):
 class Response:
  status=200
  def __init__(self):self.lines=iter(frames)
  def readline(self,n):return next(self.lines,b"")
 class Connection:
  def __init__(self,*args):pass
  def request(self,*args):pass
  def getresponse(self):return Response()
  def abort(self):pass
  def close_owned(self):self.abort()
 with patch.object(s,"CancellableConnection",Connection):
  return s.LoopbackModel("127.0.0.1",1,lambda:"PUBLIC-FAKE-KEY")([],8,5,lease or s.Lease(4))

DONE=b"data: [DONE]\n"
class StreamFrames(unittest.TestCase):
 def test_plain_and_finish_metadata(self):
  self.assertEqual("PUBLIC_OK",invoke([frame({"content":"PUBLIC_OK"}),frame({}),DONE]))
 def test_role_null_and_reasoning_are_not_final_text(self):
  self.assertEqual("PUBLIC_OK",invoke([frame({"role":"assistant","content":None}),frame({"reasoning_content":"PUBLIC_REASONING"}),frame({"content":"PUBLIC_OK"}),DONE]))
 def test_empty_choices_usage_is_ignored(self):
  self.assertEqual("PUBLIC_OK",invoke([frame({"content":"PUBLIC_OK"}),b'data: {"choices":[],"usage":{"completion_tokens":1}}\n',DONE]))
 def test_non_string_content_is_rejected(self):
  with self.assertRaises(ValueError):invoke([frame({"content":{}}),DONE])
 def test_malformed_choices_are_rejected(self):
  with self.assertRaises(ValueError):invoke([b'data: {"choices":{}}\n',DONE])
 def test_missing_done_is_rejected(self):
  with self.assertRaises(ConnectionError):invoke([frame({"content":"PUBLIC_OK"})])
 def test_cancelled_lease_is_not_bypassed_by_metadata(self):
  lease=s.Lease(4);lease.abort()
  with self.assertRaises(InterruptedError):invoke([frame({"content":None}),DONE],lease)
 def test_frame_size_bound_is_preserved(self):
  with self.assertRaises(ValueError):invoke([b"data: "+b"x"*65537+b"\n",DONE])

