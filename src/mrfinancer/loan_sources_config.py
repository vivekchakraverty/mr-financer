"""Routes and source-specific mappings for the workbook's loan directories."""

FDIC_API = "https://api.fdic.gov/banks/institutions"
REGAFI_API = "https://www.regafi.fr/api/explore/v2.1/catalog/datasets/catalogue-banque/records/"
FINTABLE_API = "https://fintable.io/api/v2/institutions"
BCB_API = (
    "https://olinda.bcb.gov.br/olinda/servico/Instituicoes_em_funcionamento/"
    "versao/v1/odata/SedesBancoComMultCE?$top=100&$format=json"
)

# These paths are published by the workbook's own directory pages.
PAGES = {
    "osfi": (
        "/en/supervision/financial-institutions/banks",
        "/en/supervision/financial-institutions/trust-loan-companies",
    ),
    "cbr": ("/banking_sector/credit/FullCoList/",),
    "fi": ("/en/vara-register/company-register/",),
    "bank-index": ("/companies/south-korea/lenders-and-credit-institutions/",),
    "cbi": ("/FirmSearchPage.aspx",),
    "bde": ("https://app.bde.es/rbe_spa/",),
    "sba": ("https://data.sba.gov/dataset/7a-504-activity-reports-current-month",),
    "sama-banks": ("/en-US/Supervision/LicenseEntities/Pages/LicensedBanks.aspx",),
}
EXTRA_HOSTS = {
    "bcb": ("olinda.bcb.gov.br",),
    "rbi-nbfc": ("rbidocs.rbi.org.in",),
    "rbi": ("rbidocs.rbi.org.in",),
    "sg-moneylenders": ("go.gov.sg", "file.go.gov.sg"),
    "sba": ("data.sba.gov", "sba-llms-prd-public.sbalenderportal.com"),
    "bde": ("app.bde.es",),
    "fdic": ("api.fdic.gov",),
    "regafi": ("www.regafi.fr", "regafi.fr"),
}
EXPORT_PATTERNS = {
    "rbi": r"foreign bank branches/representative offices",
    "rbi-nbfc": r"list_of_nbfcs_and_arcs_registered_with_the_rbi",
    "fsa-japan": r"/(?:city|reg|s_banks|moneylenders?)\.xlsx",
    "finma": r"/beh\.xlsx",
    "sg-moneylenders": r"mlaw-rom-list-licensed-moneylenders",
    "sba": r"SBA-Monthly-(?:Lender7A|CDC|TPL)Activity\.xlsx",
}
# Select the institution name explicitly on sites which use non-tabular layouts.
NAME_SELECTORS = {
    "bddk": ".baslikContainer",
    "norway": 'a[href*="/finanstilsynets-registry/details/"]',
    "fsc": ".forganization_name_con",
    "bank-index": 'a[href^="/bank/"] .rn b',
    "ojk": 'td.ms-vb-title a[href*="ListId="], [id^="scriptWPQ"] td a[href*="ListId="]',
    "mas": 'a[href*="/fid/institution/detail/"]',
    "fca": 'a[href*="/firm?id="], a[href*="/firm/"]',
    "cbi": 'a[href*="FirmDataPage"], a[href*="FirmDetails"]',
    "fi": 'a[href*="company-register/details"]',
    "bcra": "#entidades-select option[value]",
}
# Local category equivalents are used when a register's search field only accepts a firm name.
FILTERS = {
    "bafin": ("#institutKategorie", "30", "30", "#sucheButtonInstitut"),
    "cbi": ("select.firminput2", "RetailCreditHomeReversion", "CreditInstitutions",
            '[id$="_btnFirmNameSearch"]'),
}
SEARCH = {
    "mas": ("#txtSearch", "#btnSearch", "personal loans", "business loans"),
    "fca": ('input[type=search], input[placeholder*="Search" i]', None,
            "personal loans", "business loans"),
    "regafi": ('input[type=search], input[placeholder*="Rechercher" i]', None,
               "crédit à la consommation", "crédit aux entreprises"),
    "dnb": ('main input[type=search], main input[placeholder*="Search" i]', None,
            "consumentenkrediet", "zakelijke leningen"),
    "bde": ('main input[type=search], main input[placeholder*="Buscar" i]', None,
            "crédito al consumo", "préstamos a empresas"),
    "cnbv": ('main input[type=search], input[placeholder*="Buscar" i]', None,
             "crédito personal", "crédito empresarial"),
}
FSS_PATHS = {
    "personal": "/finlife/ldng/indvlCrdt/list.do?menuNo=700009",
    "business": "/finlife/ldng/indvlBusi/list.do?menuNo=700072",
}
RENDERED_LISTS = {
    "bcra", "nbb", "nfra", "boi", "norway", "sama-finance", "sama-banks", "cbi-downloads",
}

# Download URLs published directly by the linked source pages.
PUBLISHED_FILES = {
    "sg-moneylenders": ("https://file.go.gov.sg/mlaw-rom-list-licensed-moneylenders.pdf",),
    "sba": ("https://sba-llms-prd-public.sbalenderportal.com/SBA-Monthly-Lender7AActivity.xlsx",),
    "rbi-nbfc": ("https://rbidocs.rbi.org.in/rdocs/content/DOCs/List_of_NBFCs_and_ARCs_registered_with_the_RBI.XLSX",),
    "cbuae": ("https://centralbank.ae/media/hw0lreyi/cb-register-august-2026.pdf",),
}
