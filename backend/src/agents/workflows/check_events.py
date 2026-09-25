import agent_framework as af
import inspect

# Check AgentExecutorResponse
print("=== AgentExecutorResponse ===")
for attr in dir(af.AgentExecutorResponse):
    if not attr.startswith('_'):
        val = getattr(af.AgentExecutorResponse, attr, None)
        if val is not None and not callable(val):
            print(f"  {attr}: {type(val).__name__} = {repr(val)[:100]}")

# Check ResponseStream
print("\n=== ResponseStream ===")
if hasattr(af, 'ResponseStream'):
    print(inspect.signature(af.ResponseStream.__init__) if hasattr(af.ResponseStream, '__init__') else "N/A")
    if hasattr(af.ResponseStream, 'updates'):
        print(f"  updates: {af.ResponseStream.updates}")

# Check if there's a response_stream or stream wrapper
print("\n=== Check for streaming/response/executor types ===")
for item in sorted(dir(af)):
    obj = getattr(af, item)
    if isinstance(obj, type) and ('stream' in item.lower() or 'response' in item.lower() or 'executor' in item.lower()):
        print(f"  {item}")
        try:
            sig = inspect.signature(obj.__init__)
            print(f"    __init__: {sig}")
        except:
            pass
