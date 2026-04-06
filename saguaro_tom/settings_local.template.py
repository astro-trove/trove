ALLOWED_HOST = ''       # hostname or IP address of the web server (leave blank for development)
ATLAS_API_KEY = ''      # API key for the ATLAS forced photometry server
DEBUG = False           # set to True to display error tracebacks in browser, leave False in production
EMAIL_HOST = ''         # SMTP server for sending emails
EMAIL_HOST_USER = ''    # email address for the account sending emails
EMAIL_HOST_PASSWORD = ''  # password for the account sending emails (app password for Gmail)
EMAIL_PORT = 587        # port for the SMTP server
FORCE_SCRIPT_NAME = ''  # the subdomain where you will host the site (leave blank for development)
GCN_CLIENT_ID = ''      # client ID for GCN Classic over Kafka
GCN_CLIENT_SECRET = ''  # secret key for GCN Classic over Kafka
HOPSKOTCH_GROUP_ID = '' # make up a unique ID for your Hopskotch alert consumer
LASAIR_TOKEN = ''       # API key for the Lasair broker
POSTGRES_DB = ''        # name of the Postgres database
POSTGRES_HOST = ''      # hostname or IP address of the Postgres database server
POSTGRES_PASSWORD = ''  # password for the Postgres database
POSTGRES_PORT = 5432    # port number for the Postgres database
POSTGRES_USER = ''      # username for the Postgres database
SAVE_TEST_ALERTS = False # save test gravitational-wave alerts
SCIMMA_AUTH_USERNAME = '' # username for SCIMMA authentication
SCIMMA_AUTH_PASSWORD = '' # password for SCIMMA authentication
SECRET_KEY = ''         # see https://docs.djangoproject.com/en/4.1/ref/settings/#secret-key
NLE_LINKS = [  # links to TOMs for GW event pages, {nle.event_id} is replaced by the event name (e.g., S190425z)
    ('https://tom0.edu/nonlocalizedevents/{nle.event_id}/', 'Name of TOM 0'),  # TOM corresponding to Slack workspace 0
    ('https://tom1.edu/nonlocalizedevents/{nle.event_id}/', 'Name of TOM 1'),  # TOM corresponding to Slack workspace 1
]
TARGET_LINKS = [  # links to TOMs for target pages, {target.name} is replaced by the target name (e.g., AT2017gfo)
    ('https://tom0.edu/targets/{target.id}/', 'Name of TOM 0'),  # TOM corresponding to Slack workspace 0
    ('https://tom1.edu/targets/{target.name}/', 'Name of TOM 1'),  # TOM corresponding to Slack workspace 1
]
SITE_ID = 1  # set to 1 for production site, 2 for local development
SLACK_TOKENS_GW = [  # Slack API tokens for GW alerts
    '',  # Slack workspace 0
    '',  # Slack workspace 1
]
SLACK_TOKEN_EP = ''  # Slack API token for Einstein Probe alerts
SLACK_TOKEN_TNS = ''  # Slack API token for TNS transient alerts
TNS_API_KEY = ''        # API key for the Transient Name Server
TREASUREMAP_API_KEY = '' # API key for the Gravitational Wave Treasure Map
ZTF_INFO = { # Login info for sending requests to the ZTF forced photometry server
    "email_server":"imap.gmail.com",
    "email_password":"",
    "email_address":"",
    "user_address":"",
    "user_password":""
}
