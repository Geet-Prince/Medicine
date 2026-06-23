import urllib.request
import urllib.error

req = urllib.request.Request('http://localhost:5000/api/stream?q=hello')
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    print("HTTPError:", e.code)
    print(e.read().decode())
except Exception as e:
    print("Exception:", e)
