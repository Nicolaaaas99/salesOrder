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

# Performance guards (see PERFORMANCE.md).
# Any single SQL statement running longer than this is cancelled by the driver.
DB_QUERY_TIMEOUT_SECONDS = int(os.getenv("DB_QUERY_TIMEOUT_SECONDS", "30"))
# Statements slower than this are logged as "SLOW SQL" with the API path.
DB_SLOW_QUERY_SECONDS    = float(os.getenv("DB_SLOW_QUERY_SECONDS", "1.0"))
# Whole API requests slower than this are logged as "SLOW REQUEST".
SLOW_REQUEST_SECONDS     = float(os.getenv("SLOW_REQUEST_SECONDS", "2.0"))

# Order Import: how far (in rand, incl VAT) our order total may differ from
# the purchase order's total before the import is blocked. Prices are
# checked per line to the cent; this only absorbs cent-rounding on VAT
# lines (e.g. 50 x R12.26 excl = R613.00 vs the PO's R613.04).
ORDER_IMPORT_TOTAL_TOLERANCE = float(os.getenv("ORDER_IMPORT_TOTAL_TOLERANCE", "1.00"))

DLL_PATH     = os.getenv("DLL_PATH")
LICENSE_KEY  = os.getenv("LICENSE_KEY")
LICENSE_CODE = os.getenv("LICENSE_CODE")
