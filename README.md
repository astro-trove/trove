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

  3. Create virtual environment and install dependencies:

  ```bash
    % cd /var/www/saguaro_tom
    % python3 -m venv venv
    % source venv/bin/activate
    % pip install --upgrade pip
    % pip install -r requirements.txt
  ```

  4. Run the development server:

  ```bash
    % python3 manage.py collectstatic # only the first time you start the development server
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
@reboot sleep 40 && /var/www/saguaro_tom/venv/bin/python /var/www/saguaro_tom/manage.py readstreams > /home/saguaro/alertstreams.log 2>&1
```

If it does not restart, or you need to restart the listener manually, run the following on `sand`. First, kill any other instances are running:
```
pkill -f readstreams
```

Then run
```
nohup /var/www/saguaro_tom/venv/bin/python /var/www/saguaro_tom/manage.py readstreams > /home/saguaro/alertstreams.log 2>&1 &
```

## Allowing for asynchronous tasks
To run asynchronous tasks (ATLAS forced photometry queries and minor planet checking),
we use [django-tasks](https://github.com/realOrangeOne/django-tasks),
configured according to the [TOM Toolkit documentation](https://tom-toolkit.readthedocs.io/en/stable/code/backgroundtasks.html).
The asynchronous workers should automatically restart when `sand` restarts, thanks to this cronjob:
```
@reboot sleep 60 && cd /var/www/saguaro_tom/ && for i in $(seq 1 48); do venv/bin/python manage.py db_worker --queue-name '*' -v3 > /home/saguaro/django_tasks_logs/django-tasks.${i}.log 2>&1; done
```

If the workers do not restart, or you need to restart them manually, run the following on `sand`:
```
pkill -f db_worker
cd /var/www/saguaro_tom/
source venv/bin/activate
for i in $(seq 1 48)
    do nohup venv/bin/python manage.py db_worker --queue-name '*' -v3 > /home/saguaro/django_tasks_logs/django-tasks.${i}.log 2>&1 &
done
```

To view what their logs all at once, run:
```
tail -f ~/django_tasks_logs/*
```
Press Ctrl+C to exit that view.

## Other periodic tasks
Several other tasks run every hour as cronjobs (as root):
```
0 * * * * /var/www/saguaro_tom/venv/bin/python /var/www/saguaro_tom/manage.py report_pointings > /home/saguaro/report_pointings.log 2>&1
0 * * * * /var/www/saguaro_tom/venv/bin/python /var/www/saguaro_tom/manage.py updatestatus > /home/saguaro/observation_status.log 2>&1
0 * * * * /var/www/saguaro_tom/venv/bin/python /var/www/saguaro_tom/manage.py verify_listener > /home/saguaro/verify_listener.log 2>&1
5 * * * * /var/www/saguaro_tom/venv/bin/python /var/www/saguaro_tom/manage.py ingest_tns > /home/saguaro/ingest_tns.log 2>&1
```

Respectively, these:
- report survey pointings to the [Treasure Map](https://treasuremap.space)
- update the statuses of observations scheduled through the TOM (e.g., MMT)
- verify that the GW alert listener is functioning by checking that we received an alert for the latest event in [GraceDB](https://gracedb.ligo.org/latest/)
- ingest targets from the [TNS](https://wis-tns.org) (the TNS table is updated on the hour, so we run this 10 minutes after)
