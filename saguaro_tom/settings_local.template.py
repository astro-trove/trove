ALERT_EMAIL_FROM = ''   # email address from which the alerts are sent
ALERT_SMS_FROM = ''     # phone number from which the text message alerts are sent
ALLOWED_HOST = ''       # hostname or IP address of the web server (leave blank for development)
ATLAS_API_KEY = ''      # API key for the ATLAS forced photometry server
CONTACT_EMAIL = ''      # contact email for MMT observation request notes
CSS_HOSTNAME = ''       # hostname to which to send CSS .prog files
CSS_USERNAME = ''       # username (on CSS_HOSTNAME) to which to send CSS .prog files
CSS_DIRNAME = ''        # directory (on CSS_HOSTNAME) in which to put CSS .prog files
DEBUG = False           # set to True to display error tracebacks in browser, leave False in production
FORCE_SCRIPT_NAME = ''  # the subdomain where you will host the site (leave blank for development)
GCN_CLIENT_ID = ''      # client ID for GCN Classic over Kafka
GCN_CLIENT_SECRET = ''  # secret key for GCN Classic over Kafka
GEM_N_API_KEY = ''      # API key for Gemini Observatory North
GEM_S_API_KEY = ''      # API key for Gemini Observatory South
HOPSKOTCH_GROUP_ID = '' # make up a unique ID for your Hopskotch alert consumer
LASAIR_TOKEN = ''       # API key for the Lasair broker
LCO_API_KEY = ''        # API key for Las Cumbres Observatory
MMT_BINOSPEC_PROGRAMS = [] # list of (API key, human-readable name) for MMT+Binospec
MMT_MMIRS_PROGRAMS = [] # list of (API key, human-readable name) for MMT+MMIRS
POSTGRES_DB = ''        # name of the Postgres database
POSTGRES_HOST = ''      # hostname or IP address of the Postgres database server
POSTGRES_PASSWORD = ''  # password for the Postgres database
POSTGRES_PORT = 5432    # port number for the Postgres database
POSTGRES_USER = ''      # username for the Postgres database
SAVE_TEST_ALERTS = False # save test gravitational-wave alerts
SCIMMA_AUTH_USERNAME = '' # username for SCIMMA authentication
SCIMMA_AUTH_PASSWORD = '' # password for SCIMMA authentication
SECRET_KEY = ''         # see https://docs.djangoproject.com/en/4.1/ref/settings/#secret-key
SLACK_LINKS = [  # links to TOMs for Slack alerts, {nle.id} is replaced by the event name (e.g., S190425z)
    '<https://tom0.edu/nonlocalizedevents/{nle.event_id}/|Name of TOM 0>',  # TOM corresponding to Slack workspace 0
    '<https://tom1.edu/nonlocalizedevents/{nle.event_id}/|Name of TOM 1>',  # TOM corresponding to Slack workspace 1
]
SLACK_URLS = [  # list of lists of Slack URLs for incoming webhooks
    [  # list of URLs for Slack workspace 0
        'https://hooks.slack.com/services/.../.../...',  # incoming webhook for #alerts-subthreshold
        'https://hooks.slack.com/services/.../.../...',  # incoming webhook for #alerts-burst
        'https://hooks.slack.com/services/.../.../...',  # incoming webhook for #alerts-bbh
        'https://hooks.slack.com/services/.../.../...',  # incoming webhook for #alerts-ns
    ],
    [  # list of URLs for Slack workspace 1
        'https://hooks.slack.com/services/.../.../...',  # incoming webhook for #alerts-subthreshold
        'https://hooks.slack.com/services/.../.../...',  # incoming webhook for #alerts-burst
        'https://hooks.slack.com/services/.../.../...',  # incoming webhook for #alerts-bbh
        'https://hooks.slack.com/services/.../.../...',  # incoming webhook for #alerts-ns
    ],
]
SWIFT_USERNAME = ''     # username for the Swift ToO API
SWIFT_SHARED_SECRET = '' # shared secret for the Swift ToO API
TNS_API_KEY = ''        # API key for the Transient Name Server
TREASUREMAP_API_KEY = '' # API key for the Gravitational Wave Treasure Map
TWILIO_ACCOUNT_SID = '' # account ID for Twilio text message alerts
TWILIO_AUTH_TOKEN = ''  # authorization token for Twilio text message alerts
