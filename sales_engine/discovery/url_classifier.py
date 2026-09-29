from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

class URLClassifier:
    KNOWN_ATS_DOMAINS = [
        "jobs.lever.co",
        "boards.greenhouse.io",
        "jobs.ashbyhq.com",
        "workable.com",
        "breezy.hr",
        "applytojob.com"
    ]
    
    JOB_PATH_CLUES = [
        "/jobs/", "/job/", "/careers/", "/career/", "/positions/", "/openings/"
    ]
    
    @staticmethod
    def normalize_url(url: str) -> str:
        try:
            parsed = urlparse(url)
            # Remove fragment
            parsed = parsed._replace(fragment="")
            
            # Remove tracking params like utm_
            query_params = parse_qsl(parsed.query)
            filtered_params = [(k, v) for k, v in query_params if not k.startswith("utm_")]
            parsed = parsed._replace(query=urlencode(filtered_params))
            
            # Remove trailing slash from path for consistency
            path = parsed.path
            if path != "/" and path.endswith("/"):
                parsed = parsed._replace(path=path.rstrip("/"))
                
            return urlunparse(parsed)
        except Exception:
            return url
            
    def classify(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            path = parsed.path.lower()
            
            for ats in self.KNOWN_ATS_DOMAINS:
                if ats in domain:
                    return "ats_job"
                    
            for clue in self.JOB_PATH_CLUES:
                if clue in path or path.endswith(clue.rstrip("/")):
                    if "job" in clue or "position" in clue or "opening" in clue:
                        return "job_posting"
                    return "career_page"
                    
            return "unknown"
        except Exception:
            return "unknown"
