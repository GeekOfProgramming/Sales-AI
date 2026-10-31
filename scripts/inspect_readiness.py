"""
scripts/inspect_readiness.py
SalesAI S3.5.4A — Read-Only Metadata & Readiness Probe for Cohere, NVIDIA NIM, and Cloudflare.
Strictly zero-generation: ONLY read-only GET requests to inspect authentication, catalogs, and account metadata.
Zero-secret: Never displays or logs raw API keys, tokens, or account credentials.
"""

import json
import os
import sys
import httpx

def mask_secret(secret: str) -> str:
    if not secret:
        return "<MISSING>"
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}...{secret[-4:]} (len={len(secret)})"

def inspect_cohere():
    print("=" * 60)
    print("COHERE READINESS INSPECTION (Candidate t2_c2)")
    print("=" * 60)
    api_key = os.environ.get("COHERE_API_KEY")
    if not api_key:
        print("COHERE_API_KEY is not set.")
        return

    print(f"COHERE_API_KEY loaded: {mask_secret(api_key)}")
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Accept": "application/json",
        "User-Agent": "SalesAI-Readiness-Audit/1.0",
    }

    # 1. Probe v1 models endpoint
    with httpx.Client(timeout=20.0) as client:
        try:
            resp = client.get("https://api.cohere.com/v1/models?page_size=100", headers=headers)
            print(f"GET https://api.cohere.com/v1/models -> HTTP {resp.status_code}")
            print("Headers:")
            for k, v in resp.headers.items():
                if any(x in k.lower() for x in ["ratelimit", "limit", "trial", "quota", "usage", "credit"]):
                    print(f"  {k}: {v}")

            data = resp.json()
            models = data.get("models", [])
            print(f"Total models returned: {len(models)}")
            target_model = "command-r-08-2024"
            found = [m for m in models if m.get("name") == target_model]
            if found:
                m_info = found[0]
                print(f"Target model '{target_model}' found in catalog:")
                print(f"  Name: {m_info.get('name')}")
                print(f"  Endpoints: {m_info.get('endpoints')}")
                print(f"  Context length: {m_info.get('context_length')}")
                print(f"  Tokenizer URL: {m_info.get('tokenizer_url')}")
                print(f"  Full spec: {json.dumps(m_info, indent=2)}")
            else:
                print(f"Target model '{target_model}' NOT found in models list.")
                print("Available Command models:")
                for m in models:
                    if "command" in m.get("name", "").lower():
                        print(f"  - {m.get('name')}: endpoints={m.get('endpoints')}")
        except Exception as e:
            print(f"Cohere v1 models probe error: {e}")

        # 2. Probe v2 models endpoint if supported
        try:
            resp_v2 = client.get("https://api.cohere.com/v2/models", headers=headers)
            print(f"GET https://api.cohere.com/v2/models -> HTTP {resp_v2.status_code}")
            if resp_v2.status_code == 200:
                print(f"V2 models: {json.dumps(resp_v2.json(), indent=2)[:500]}")
        except Exception as e:
            print(f"Cohere v2 models error: {e}")

        # 3. Probe check-api-key or key-info endpoints if any exist
        for test_url in [
            "https://api.cohere.com/v1/check-api-key",
            "https://api.cohere.com/v1/api-key",
            "https://api.cohere.com/v2/check-api-key",
        ]:
            try:
                r = client.get(test_url, headers=headers)
                print(f"GET {test_url} -> HTTP {r.status_code}")
                if r.status_code in (200, 400, 403, 404):
                    print(f"  Response: {r.text[:200]}")
            except Exception:
                pass


def inspect_nvidia():
    print("\n" + "=" * 60)
    print("NVIDIA NIM READINESS INSPECTION (Candidate t2_c3)")
    print("=" * 60)
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        print("NVIDIA_API_KEY is not set.")
        return

    print(f"NVIDIA_API_KEY loaded: {mask_secret(api_key)}")
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Accept": "application/json",
        "User-Agent": "SalesAI-Readiness-Audit/1.0",
    }

    with httpx.Client(timeout=20.0) as client:
        try:
            resp = client.get("https://integrate.api.nvidia.com/v1/models", headers=headers)
            print(f"GET https://integrate.api.nvidia.com/v1/models -> HTTP {resp.status_code}")
            print("Headers:")
            for k, v in resp.headers.items():
                if any(x in k.lower() for x in ["ratelimit", "limit", "trial", "quota", "usage", "credit", "nvidia", "nvcf"]):
                    print(f"  {k}: {v}")

            data = resp.json()
            models = data.get("data", [])
            print(f"Total catalog models returned: {len(models)}")
            all_ids = sorted([m.get("id") for m in models if m.get("id")])
            print("All NVIDIA model IDs:")
            for mid in all_ids:
                print(f"  {mid}")
            target_model = "meta/llama-3.3-70b-instruct"
            found = [m for m in models if m.get("id") == target_model]
            if found:
                print(f"Target model '{target_model}' found in catalog:")
                print(f"  Model metadata: {json.dumps(found[0], indent=2)}")
            else:
                print(f"Target model '{target_model}' NOT found in catalog.")
                print("Llama-3 models found:")
                for m in models:
                    if "llama-3" in m.get("id", "").lower():
                        print(f"  - {m.get('id')}")
        except Exception as e:
            print(f"NVIDIA models probe error: {e}")

        # Check credits / account info endpoint if any
        for test_url in [
            "https://integrate.api.nvidia.com/v1/org",
            "https://integrate.api.nvidia.com/v1/account",
            "https://integrate.api.nvidia.com/v1/credits",
            "https://api.nvcf.nvidia.com/v2/nvcf/authorizations",
        ]:
            try:
                r = client.get(test_url, headers=headers)
                print(f"GET {test_url} -> HTTP {r.status_code}")
                if r.status_code == 200:
                    print(f"  Response: {r.text[:300]}")
            except Exception:
                pass


def inspect_cloudflare():
    print("\n" + "=" * 60)
    print("CLOUDFLARE WORKERS AI READINESS INSPECTION (Candidate t2_c4)")
    print("=" * 60)
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account_id:
        print(f"CLOUDFLARE_API_TOKEN set: {bool(token)}, CLOUDFLARE_ACCOUNT_ID set: {bool(account_id)}")
        return

    print(f"CLOUDFLARE_API_TOKEN loaded: {mask_secret(token)}")
    print(f"CLOUDFLARE_ACCOUNT_ID loaded: {account_id[:4]}...{account_id[-4:]} (len={len(account_id)})")
    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/json",
        "User-Agent": "SalesAI-Readiness-Audit/1.0",
    }

    with httpx.Client(timeout=20.0) as client:
        # 1. Verify token
        try:
            resp_tok = client.get("https://api.cloudflare.com/client/v4/user/tokens/verify", headers=headers)
            print(f"GET /user/tokens/verify -> HTTP {resp_tok.status_code}")
            if resp_tok.status_code == 200:
                tok_data = resp_tok.json()
                print(f"Token status: {tok_data.get('messages', [])}")
                print(f"Result: {tok_data.get('result', {})}")
        except Exception as e:
            print(f"Token verify error: {e}")

        # 2. Check account details
        try:
            resp_acc = client.get(f"https://api.cloudflare.com/client/v4/accounts/{account_id}", headers=headers)
            print(f"GET /accounts/{account_id} -> HTTP {resp_acc.status_code}")
            if resp_acc.status_code == 200:
                acc_data = resp_acc.json().get("result", {})
                print(f"Account name: {acc_data.get('name')}")
                print(f"Account type: {acc_data.get('type')}")
                print(f"Account settings: {acc_data.get('settings', {})}")
            else:
                print(f"Account get error: {resp_acc.text[:200]}")
        except Exception as e:
            print(f"Account check error: {e}")

        # 3. Check account subscriptions / plan
        try:
            resp_sub = client.get(f"https://api.cloudflare.com/client/v4/accounts/{account_id}/subscriptions", headers=headers)
            print(f"GET /accounts/{account_id}/subscriptions -> HTTP {resp_sub.status_code}")
            if resp_sub.status_code == 200:
                subs = resp_sub.json().get("result", [])
                print(f"Subscriptions count: {len(subs)}")
                for s in subs:
                    print(f"  - App: {s.get('app')}, Rate Plan: {s.get('rate_plan')}, State: {s.get('state')}")
        except Exception as e:
            print(f"Subscriptions error: {e}")

        # 4. Search Workers AI models
        try:
            target_model = "@cf/meta/llama-3.1-8b-instruct"
            resp_models = client.get(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search?per_page=100",
                headers=headers
            )
            print(f"GET /accounts/{account_id}/ai/models/search?per_page=100 -> HTTP {resp_models.status_code}")
            if resp_models.status_code == 200:
                res_models = resp_models.json().get("result", [])
                print(f"Found {len(res_models)} models in catalog:")
                target_found = False
                for m in res_models:
                    m_name = m.get("name")
                    is_target = (m_name == target_model)
                    if is_target:
                        target_found = True
                    if any(x in m_name.lower() for x in ["llama", "instruct", "meta", "cf"]):
                        print(f"  - {m_name} (task={m.get('task', {}).get('name') if isinstance(m.get('task'), dict) else m.get('task')}) {'[EXACT TARGET]' if is_target else ''}")
                    if is_target:
                        print(f"    Full Target Details: {json.dumps(m, indent=2)}")
                if not target_found:
                    print(f"Target model '{target_model}' was NOT found in catalog.")
        except Exception as e:
            print(f"Models search error: {e}")

        # 5. Check Workers AI usage / neuron allocation
        for test_path in [
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/limits",
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/usage",
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/workers/account-settings",
        ]:
            try:
                r = client.get(test_path, headers=headers)
                print(f"GET {test_path} -> HTTP {r.status_code}")
                if r.status_code == 200:
                    print(f"  Result: {r.text[:300]}")
            except Exception:
                pass


if __name__ == "__main__":
    inspect_cohere()
    inspect_nvidia()
    inspect_cloudflare()
