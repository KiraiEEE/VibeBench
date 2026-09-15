row 132   anthropics/claude-agent-sdk-python#714
============================================================================
CHAIN in claude_agent_sdk._internal.query.Query._read_messages   kind=value_switch subject='msg_type' branches=3
QUESTION: is the set of variants OPEN (a new one is expected, and
          would force editing this chain) or CLOSED by nature?
SHOWING THE WHOLE ENCLOSING FUNCTION -- a branch count alone cannot answer this.
>>> marks the first line of the dispatch chain itself.

file src/claude_agent_sdk/_internal/query.py
      172      async def _read_messages(self) -> None:
      173          """Read messages from transport and route them."""
      174          try:
      175              async for message in self.transport.read_messages():
      176                  if self._closed:
      177                      break
      178  
      179                  msg_type = message.get("type")
      180  
      181                  # Route control messages
>>>   182                  if msg_type == "control_response":
      183                      response = message.get("response", {})
      184                      request_id = response.get("request_id")
      185                      if request_id in self.pending_control_responses:
      186                          event = self.pending_control_responses[request_id]
      187                          if response.get("subtype") == "error":
      188                              self.pending_control_results[request_id] = Exception(
      189                                  response.get("error", "Unknown error")
      190                              )
      191                          else:
      192                              self.pending_control_results[request_id] = response
      193                          event.set()
      194                      continue
      195  
      196                  elif msg_type == "control_request":
      197                      # Handle incoming control requests from CLI
      198                      # Cast message to SDKControlRequest for type safety
      199                      request: SDKControlRequest = message  # type: ignore[assignment]
      200                      if self._tg:
      201                          self._tg.start_soon(self._handle_control_request, request)
      202                      continue
      203  
      204                  elif msg_type == "control_cancel_request":
      205                      # Handle cancel requests
      206                      # TODO: Implement cancellation support
      207                      continue
      208  
      209                  # Track results for proper stream closure
      210                  if msg_type == "result":
      211                      self._first_result_event.set()
      212  
      213                  # Regular SDK messages go to the stream
      214                  await self._message_send.send(message)
      215  
      216          except anyio.get_cancelled_exc_class():
      217              # Task was cancelled - this is expected behavior
      218              logger.debug("Read task cancelled")
      219              raise  # Re-raise to properly handle cancellation
      220          except Exception as e:
      221              logger.error(f"Fatal error in message reader: {e}")
      222              # Signal all pending control requests so they fail fast instead of timing out
      223              for request_id, event in list(self.pending_control_responses.items()):
      224                  if request_id not in self.pending_control_results:
      225                      self.pending_control_results[request_id] = e
      226                      event.set()
      227              # Put error in stream so iterators can handle it
      228              await self._message_send.send({"type": "error", "error": str(e)})
      229          finally:
      230              # Unblock any waiters (e.g. string-prompt path waiting for first
      231              # result) so they don't stall for the full timeout on early exit.
      232              self._first_result_event.set()
      233              # Always signal end of stream
      234              await self._message_send.send({"type": "end"})