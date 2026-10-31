import pytest
from unittest.mock import patch, MagicMock
import socket
import ssl

from sales_engine.research.lead_researcher import (
    validate_public_url,
    is_ip_address_safe,
    SSRFValidationError,
    extract_website_public_facts,
    create_lead_from_researched_website,
)
import ipaddress


def test_ip_address_safety():
    """Verify is_ip_address_safe rejects private, loopback, link-local, reserved."""
    assert is_ip_address_safe(ipaddress.ip_address("127.0.0.1")) is False
    assert is_ip_address_safe(ipaddress.ip_address("10.0.0.1")) is False
    assert is_ip_address_safe(ipaddress.ip_address("172.16.0.1")) is False
    assert is_ip_address_safe(ipaddress.ip_address("192.168.1.1")) is False
    assert is_ip_address_safe(ipaddress.ip_address("169.254.169.254")) is False
    assert is_ip_address_safe(ipaddress.ip_address("0.0.0.0")) is False
    assert is_ip_address_safe(ipaddress.ip_address("::1")) is False
    assert is_ip_address_safe(ipaddress.ip_address("fe80::1")) is False
    assert is_ip_address_safe(ipaddress.ip_address("fc00::1")) is False

    # Safe public IPs
    assert is_ip_address_safe(ipaddress.ip_address("8.8.8.8")) is True
    assert is_ip_address_safe(ipaddress.ip_address("93.184.216.34")) is True


def test_validate_public_url_blocks_internal():
    """Verify validate_public_url blocks dangerous schemes and loopback/private hosts."""
    # Disallowed schemes
    with pytest.raises(SSRFValidationError, match="Unsupported URL scheme"):
        validate_public_url("file:///etc/passwd")

    with pytest.raises(SSRFValidationError, match="Unsupported URL scheme"):
        validate_public_url("ftp://example.com")

    # Localhost and loopback
    with pytest.raises(SSRFValidationError, match="loopback or local address"):
        validate_public_url("http://localhost:8000/api")

    with pytest.raises(SSRFValidationError, match="loopback or local address"):
        validate_public_url("http://127.0.0.1:8080/admin")

    with pytest.raises(SSRFValidationError, match="Internal domain zone"):
        validate_public_url("http://service.internal/status")

    with pytest.raises(SSRFValidationError, match="Internal domain zone"):
        validate_public_url("http://dev.local/data")


def test_validate_public_url_dns_resolution_private():
    """Verify that hostnames resolving to private IPs are blocked."""
    with patch("socket.getaddrinfo") as mock_dns:
        # Mock DNS returning private IP 10.0.0.5
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))
        ]
        with pytest.raises(SSRFValidationError, match="resolves to private/internal IP"):
            validate_public_url("https://malicious-rebinding.example.com")


def test_extract_website_public_facts_ssrf_blocked():
    """Verify extract_website_public_facts returns BLOCKED_UNSAFE for private targets without network requests."""
    res = extract_website_public_facts("http://127.0.0.1:8080/admin", "Evil Inc.")
    assert res["status"] == "BLOCKED_UNSAFE"
    assert "loopback" in res["error_message"] or "private" in res["error_message"]

    res_local = extract_website_public_facts("http://localhost:9000", "Localhost Test")
    assert res_local["status"] == "BLOCKED_UNSAFE"


def test_extract_website_public_facts_tls_failure_handled():
    """Verify TLS errors are caught gracefully and return FETCH_FAILED without crashing."""
    with patch("sales_engine.research.lead_researcher.validate_public_url") as mock_val:
        mock_val.return_value = ("https://bad-ssl.example.com", "bad-ssl.example.com", 443)
        with patch("urllib.request.build_opener") as mock_opener:
            mock_inst = MagicMock()
            mock_inst.open.side_effect = ssl.SSLCertVerificationError("Certificate verify failed: self-signed")
            mock_opener.return_value = mock_inst

            res = extract_website_public_facts("https://bad-ssl.example.com", "Bad SSL Corp")
            assert res["status"] == "FETCH_FAILED"
            assert "Certificate verify failed" in res["error_message"]


def test_create_lead_from_researched_website_semantics():
    """Verify factual verification semantics: fetch success -> EXTRACTED (not Verified)."""
    with patch("sales_engine.research.lead_researcher.extract_website_public_facts") as mock_extract:
        mock_extract.return_value = {
            "canonical_domain": "studiomiotto.it",
            "source_url": "https://www.studiomiotto.it",
            "company_name": "Studio Miotto",
            "telephone_numbers": "+39 049 123456",
            "contact_emails": "info@studiomiotto.it",
            "vat_number": "01234567890",
            "meta_description": "Ingegneria e architettura BIM",
            "page_title": "Studio Miotto BIM",
            "status": "FETCH_SUCCESS",
            "error_message": None,
        }

        rec = create_lead_from_researched_website("Studio Miotto", "https://www.studiomiotto.it")
        assert rec.verification_status == "EXTRACTED", "Fetch success must be EXTRACTED, not Verified"
        assert rec.confidence_score == 0.50
        assert rec.legal_identity == "P.IVA 01234567890"
        assert rec.telephone_numbers == "+39 049 123456"
        assert rec.contact_email_addresses == "info@studiomiotto.it"
