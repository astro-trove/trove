# SAGUARO TOM #

Welcome to the SAGUARO target and observation manager for GW follow-up.

## Repositories of Interest:

  * https://github.com/SAGUARO-MMA/saguaro_tom.git
  * https://github.com/SAGUARO-MMA/sassy_q3c_models.git
  * https://github.com/SAGUARO-MMA/kne-cand-vetting.git

## Installation (for development)

 1. Clone the repository:

  ```bash
    % cd /var/www
    % git clone https://github.com/SAGUARO-MMA/saguaro_tom.git
  ```

 2. Copy settings_local.template.py to settings_local.py and edit as you see fit:

  ```bash
    % cd /var/www/saguaro_tom/saguaro_tom
    % cp settings_local.template.py settings_local.py
    % vi settings_local.py
  ```

  3. Install dependencies:

  ```bash
    % python3 -m pip install --upgrade pip
    % python3 -m pip install -r requirements.txt
  ```

  4. Run the development server:

  ```bash
    % cd /var/www/saguaro_tom
    % python3 manage.py runserver
  ```

## Enabling WSGI under Apache2

Enable WSGI within Apache2 in the usual way (there are plenty of web tutorials on this).

Edit `saguaro_tom.conf` and `saguaro_tom-ssl.conf` as you see fit to reflect your source
code directory and security certificates. You should probably rename 'localhost' to something
more suitable. If you do, you will also need to add that name to /etc/hosts and set 
`ALLOWED_HOST` in `settings_local.py`. If you are hosting from a subdomain, you must also set
`FORCE_SCRIPT_NAME` in `settings_local.py`. 


Then (as root) enable the sites:

  ```bash
    % cd /var/www/saguaro_tom
    % cp saguaro_tom.conf /etc/apache2/sites-available/
    % cp saguaro_tom-ssl.conf /etc/apache2/sites-available/
    % cd /etc/apache2/sites-available/
    % apache2ctl configtest
    % a2ensite saguaro_tom.conf
    % a2ensite saguaro_tom-ssl.conf
    % service apache2 restart
  ```

## Running the alert listener
The alert listener is now integrated into the TOM. It should automatically restart when `sand` restarts, thanks to this cronjob (run as root):
```
@reboot cd /var/www/saguaro_tom/; python manage.py readstreams > /home/saguaro/alertstreams.log 2>&1
```

If it does not restart, or you need to restart the listener manually, run the following on `sand`. First, kill any other instances are running:
```
sudo pkill -f readstreams
```

Then run
```
cd /var/www/saguaro_tom/
sudo nohup python manage.py readstreams > /home/saguaro/alertstreams.log 2>&1 &
```

## Allowing for asynchronous tasks
We use ![redis](https://redis.io) and ![dramatiq](https://dramatiq.io) to run asynchronous tasks (e.g., ATLAS forced photometry queries).
Redis is installed according to the instructions on its ![readme](https://github.com/redis/redis).
Dramatiq is configured according to the ![TOM Toolkit documentation](https://tom-toolkit.readthedocs.io/en/stable/managing_data/forced_photometry.html#configuring-your-tom-to-serve-tasks-asynchronously).
These should automatically restart when `sand` restarts, thanks to this cronjob (run as root):
```
@reboot /usr/local/bin/redis-server > /home/saguaro/redis.log 2>&1
@reboot cd /var/www/saguaro_tom/; python manage.py rundramatiq > /home/saguaro/dramatiq.log 2>&1
```

If either of these does not restart, or you need to restart them manually, run the following on `sand`. First, kill any other instances are running:
```
sudo pkill -f redis-server
sudo pkill -f rundramatiq
```

Then run
```
sudo nohup redis-server > /home/saguaro/redis.log 2>&1
cd /var/www/saguaro_tom/
sudo nohup python manage.py rundramatiq > /home/saguaro/dramatiq.log 2>&1
```

## Other periodic tasks
Several other tasks run every hour as cronjobs (as root):
```
0 * * * * /var/www/saguaro_tom/manage.py report_pointings > /home/saguaro/report_pointings.log 2>&1
0 * * * * /var/www/saguaro_tom/manage.py updatestatus > /home/saguaro/observation_status.log 2>&1
0 * * * * /var/www/saguaro_tom/manage.py verify_listener --max-seconds 10000 > /home/saguaro/verify_listener.log 2>&1
10 * * * * /var/www/saguaro_tom/manage.py ingest_tns > /home/saguaro/ingest_tns.log 2>&1
```

Respectively, these:
- report survey pointings to the ![Treasure Map](https://treasuremap.space)
- update the statuses of observations scheduled through the TOM (e.g., MMT)
- verify that the GW alert listener is functioning by checking that we received a test alert in the last 10000 seconds
- ingest targets from the ![TNS](https://wis-tns.org) (the TNS table is updated on the hour, so we run this 10 minutes after)
