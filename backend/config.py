import os
from dotenv import load_dotenv

load_dotenv()

# Company DB — the tenant's live Evolution database
SERVER           = os.getenv("DB_SERVER")
DB_USERNAME      = os.getenv("DB_USERNAME")
DB_PASSWORD      = os.getenv("DB_PASSWORD")
COMPANY_DATABASE = os.getenv("COMPANY_DATABASE")

# Common DB — SageCommon (licenses, users, etc.).
# Can live on a different server; falls back to the company server if unset.
COMMON_SERVER    = os.getenv("COMMON_SERVER",   SERVER)
COMMON_USERNAME  = os.getenv("COMMON_USERNAME", DB_USERNAME)
COMMON_PASSWORD  = os.getenv("COMMON_PASSWORD", DB_PASSWORD)
COMMON_DATABASE  = os.getenv("COMMON_DATABASE", "SageCommon")

DLL_PATH     = os.getenv("DLL_PATH")
LICENSE_KEY  = os.getenv("LICENSE_KEY")
LICENSE_CODE = os.getenv("LICENSE_CODE")
