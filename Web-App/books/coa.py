"""The starter Chart of Accounts for a take-out and delivery pizza shop (plan §8.1).

Each account has its Schedule C line (`tax_line`, plan §10.3) and its cash-flow
class (plan §9), so reports and tax worksheets are pure lookups. The owner can
edit names, descriptions and add accounts on the Setup screen.
"""

# number, name, type, subtype, tax_line, cash_flow_class, description ("use for")
STARTER_COA = [
    ("1000", "Cash on hand (register)", "asset", "cash", None, "cash",
     "Cash in the register: cash sales, cash paid out for purchases, tip-outs, cash drops"),
    ("1010", "Business checking", "asset", "cash", None, "cash",
     "The business bank account; every bank statement line"),
    ("1020", "Petty cash", "asset", "cash", None, "cash",
     "Petty cash box; small cash purchases recorded on petty cash slips"),
    ("1050", "Undeposited funds", "asset", "clearing", None, "operating",
     "Checks received and cash drops not yet deposited; cleared by the bank deposit"),
    ("1100", "Card sales clearing (Square)", "asset", "clearing", None, "operating",
     "Square card sales net of fees, waiting for the Square payout; cleared by the bank deposit"),
    ("1110", "Delivery platform receivable", "asset", "clearing", None, "operating",
     "DoorDash / Uber Eats / Grubhub payouts owed to the shop; cleared by the bank deposit"),
    ("1150", "Accounts receivable", "asset", "receivable", None, "operating",
     "Amounts customers owe, e.g. a catering check that bounced"),
    ("1200", "Inventory (year-end count)", "asset", "inventory", None, "operating",
     "Food and packaging on hand at a count date (year-end adjustment only)"),
    ("1300", "Prepaid expenses", "asset", "prepaid", None, "operating",
     "Insurance or rent paid in advance"),
    ("1500", "Kitchen equipment", "asset", "fixed_asset", None, "investing",
     "Ovens, mixers, refrigeration costing at least the capitalization threshold per item"),
    ("1510", "Furniture & fixtures", "asset", "fixed_asset", None, "investing",
     "Tables, counters, signs costing at least the capitalization threshold per item"),
    ("1520", "Vehicles", "asset", "fixed_asset", None, "investing",
     "A delivery vehicle owned by the business"),
    ("1590", "Accumulated depreciation", "asset", "contra_asset", None, "operating",
     "Depreciation to date on fixed assets (credit balance)"),
    ("2000", "Accounts payable", "liability", "payable", None, "operating",
     "Vendor bills received but not paid"),
    ("2100", "Business credit card payable", "liability", "card", None, "operating",
     "The business credit card balance; card statement lines, payments from the bank"),
    ("2150", "Purchases awaiting statement (card/bank)", "liability", "clearing", None, "operating",
     "Receipts paid by card, debit card or check, booked before the statement line arrives; the statement line clears it"),
    ("2200", "CT sales tax payable", "liability", "sales_tax", None, "operating",
     "Sales tax collected from customers (7.35% meals); cleared by the OS-114 payment"),
    ("2210", "Tips payable", "liability", "tips", None, "operating",
     "Card tips owed to staff until paid out (cash tip-out or payroll)"),
    ("2300", "Payroll liabilities", "liability", "payroll", None, "operating",
     "Net pay and payroll taxes owed, from the payroll provider report; cleared by the provider's bank debits"),
    ("2500", "Loans payable", "liability", "loan", None, "financing",
     "Equipment or SBA loan principal"),
    ("3000", "Owner's capital", "equity", "capital", None, "financing",
     "Money the owner puts into the business"),
    ("3100", "Owner's draws", "equity", "draws", None, "financing",
     "Money the owner takes out, and personal purchases paid by the business (never an expense)"),
    ("3200", "Opening balance equity", "equity", "opening", None, None,
     "Balancing figure for the opening balances only"),
    ("3900", "Retained earnings", "equity", "retained", None, None,
     "Prior years' profit (system account)"),
    ("4000", "Food & beverage sales - in store / take-out", "revenue", "sales", "1", None,
     "Counter, phone and take-out sales, catering; before sales tax"),
    ("4010", "Food & beverage sales - own delivery", "revenue", "sales", "1", None,
     "Orders delivered by the shop's own drivers; before sales tax"),
    ("4020", "Food & beverage sales - delivery platforms", "revenue", "sales", "1", None,
     "DoorDash / Uber Eats / Grubhub order subtotals (gross, before platform fees)"),
    ("4050", "Delivery fee income", "revenue", "sales", "1", None,
     "The shop's own delivery fee charged to customers (taxable, decision D8)"),
    ("4090", "Discounts, comps & refunds", "revenue", "contra_revenue", "2", None,
     "Coupons, discounts, comped food, refunds and voids (debit balance)"),
    ("5010", "COGS - Food purchases", "cogs", "cogs", "36", None,
     "Cheese, flour, meat, produce, sauce and other food ingredients"),
    ("5020", "COGS - Beverages", "cogs", "cogs", "36", None,
     "Soda, water and other drinks for resale"),
    ("5030", "COGS - Packaging & paper goods", "cogs", "cogs", "36", None,
     "Pizza boxes, cups, napkins, bags, containers"),
    ("6000", "Wages", "expense", "payroll", "26", "operating",
     "Gross wages of employees (from the payroll provider report); never the owner"),
    ("6010", "Payroll taxes", "expense", "payroll", "23", "operating",
     "Employer share of payroll taxes"),
    ("6100", "Rent", "expense", "occupancy", "20b", "operating",
     "Rent of the shop premises"),
    ("6110", "Utilities (gas, electric, water)", "expense", "occupancy", "25", "operating",
     "Gas, electricity, water"),
    ("6120", "Telephone & internet", "expense", "occupancy", "25", "operating",
     "Phone lines, internet, POS data plan"),
    ("6200", "Card processing fees", "expense", "fees", "10", "operating",
     "Square processing fees"),
    ("6210", "Delivery platform commissions & fees", "expense", "fees", "10", "operating",
     "DoorDash / Uber Eats / Grubhub commissions, marketing fees, promotions paid by the shop, error charges"),
    ("6300", "Repairs & maintenance", "expense", "operating", "21", "operating",
     "Oven repair, plumbing, equipment service"),
    ("6310", "Cleaning & kitchen supplies", "expense", "operating", "22", "operating",
     "Cleaning products, gloves, towels, small kitchen supplies"),
    ("6320", "Smallwares & small equipment", "expense", "operating", "22", "operating",
     "Pans, peels, utensils and equipment under the capitalization threshold"),
    ("6400", "Vehicle & delivery expense", "expense", "vehicle", "9", "operating",
     "Gas, tolls, parking and repairs for delivery; mileage paid to drivers"),
    ("6410", "Advertising & marketing", "expense", "operating", "8", "operating",
     "Flyers, online ads, sponsorships"),
    ("6420", "Business meals (50% deductible)", "expense", "operating", "24b", "operating",
     "Meals with suppliers or staff meetings, including the tip"),
    ("6500", "Insurance", "expense", "operating", "15", "operating",
     "Business liability, property and vehicle insurance (not health)"),
    ("6510", "Licenses, permits & fees", "expense", "operating", "23", "operating",
     "Health permit, CT LLC annual report fee, sales tax permit"),
    ("6520", "Professional fees", "expense", "operating", "17", "operating",
     "Accountant, bookkeeper, legal"),
    ("6600", "Depreciation expense", "expense", "noncash", "13", "operating",
     "Depreciation of fixed assets (monthly or year-end adjusting entry)"),
    ("6700", "Bank & card fees", "expense", "operating", "27a", "operating",
     "Bank service charges, NSF fees, card annual and late fees"),
    ("6710", "Interest expense", "expense", "operating", "16b", "operating",
     "Loan interest and credit card interest"),
    ("6900", "Miscellaneous", "expense", "operating", "27a", "operating",
     "Anything else; requires a memo"),
]

TYPES_DEBIT_NORMAL = {"asset", "expense", "cogs"}


def normal_sign(acct_type):
    """+1 for debit-normal accounts, -1 for credit-normal ones."""
    return 1 if acct_type in TYPES_DEBIT_NORMAL else -1


def load_starter(conn):
    for row in STARTER_COA:
        conn.execute(
            "INSERT OR IGNORE INTO accounts(number, name, type, subtype, tax_line, cash_flow_class, description)"
            " VALUES (?,?,?,?,?,?,?)", row)


def accounts(conn, active_only=True):
    sql = "SELECT * FROM accounts" + (" WHERE active=1" if active_only else "") + " ORDER BY number"
    return [dict(r) for r in conn.execute(sql)]


def account_map(conn):
    return {a["number"]: a for a in accounts(conn, active_only=False)}


def coa_text(conn):
    """The CHART OF ACCOUNTS block of the Bookkeeper prompt (plan §6.1)."""
    lines = []
    for a in accounts(conn):
        lines.append(f"{a['number']} | {a['name']} | {a['type']} | use for: {a['description'] or ''}")
    return "\n".join(lines)
