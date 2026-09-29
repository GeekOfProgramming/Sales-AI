from urllib.parse import urlparse

def detect_source(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        
        if host == "jobs.lever.co":
            return "lever"
            
        if host == "boards.greenhouse.io":
            return "greenhouse"
            
        if host == "jobs.ashbyhq.com":
            return "ashby"
            
        return "generic"
    except Exception:
        return "generic"
