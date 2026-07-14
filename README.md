# TROVE #

Welcome to the Treasure TROVE: a Tool for Rapid Object Vetting and Examination!

## Repositories of Interest:

  * https://github.com/astro-trove/candidate_vetting
  * https://github.com/astro-trove/trove-mpc
  * https://github.com/SAGUARO-MMA/saguaro_tom

## Installation (for development)

  **Prerequisites:** Python 3.11 or 3.12 (`django-autocomplete-light==4.0.0` requires 3.11+, and the `tom-nonlocalizedevents` dependency does not support 3.13+). A database connection is required for the application (reach out to existing developers or create a local PostgreSQL or SQLite database with sample data).

 1. Clone the repository:

  ```bash
    % cd /var/www
    % git clone https://github.com/astro-trove/trove.git
  ```

 2. Copy settings_local.template.py to settings_local.py and edit as needed. At minimum, set `SECRET_KEY`, and the `POSTGRES_*` database settings so the app can connect. For local development you may set `DEBUG = True`. For certain functionality, you will need to talk to the TROVE team to find out how to set certain environmental variables (if you want to connect to a mirror of the production database).

  ```bash
    % cd /var/www/trove/trove_tom
    % cp settings_local.template.py settings_local.py
    % vi settings_local.py
  ```

  3. Create virtual environment and install dependencies.

  **Option A — Conda** (from the project root, so `requirements.txt` is found):

  ```bash
    % cd /var/www/trove
    % conda env create -f environment.yml
    % conda activate trove
  ```

  **Option B — venv + pip:**

  ```bash
    % cd /var/www/trove
    % python3 -m venv venv # name venv whatever you want, e.g., trove
    % source venv/bin/activate
    % pip install --upgrade pip
    % pip install -r requirements.txt
  ```

  4. Apply migrations. Use **PostgreSQL** for the database; the project's migrations are not fully compatible with SQLite (e.g. `tom_nonlocalizedevents.0016` can raise "near None: syntax error" on SQLite). On a **fresh database**, the packaged migration `tom_targets.0025` (RunPython) can run before `guardian` or `trove_targets` exist in the migration state, and faking it can trigger `post_migrate` before `tom_common` is applied (causing "no such table: tom_common_profile"). So run a full migrate first (it will fail on 0025), then fake 0025, then migrate again:

  ```bash
    % python3 manage.py migrate
  ```

  If you use **SQLite**, run **`python manage.py repair_migrate`** instead of `migrate` alone. It creates missing tables (e.g. `tom_nonlocalizedevents_superevent`), fakes renames when the target table already exists, and retries migrate by faking any migration that fails with "table already exists" or "no such column" until migrate succeeds. For one-off fixes you can still use `python manage.py repair_superevent_table` then `migrate`. Prefer PostgreSQL to avoid these issues.

  5. Run the development server. Milky Way extinction uses a cached SFD dust map if present; otherwise `mwebv` defaults to 0. To download the map once, run with `FETCH_DUSTMAPS=1` (network access required). CI sets `SKIP_DUSTMAP=1` so tests never depend on that download.

  ```bash
    % python3 manage.py collectstatic # only the first time you start the development server
    % python3 manage.py runserver
  ```

  Then open **http://127.0.0.1:8000/** or **http://localhost:8000/** in your browser to view the TROVE. If you use PostgreSQL, create the database first (e.g. `createdb trove`) and set the `POSTGRES_*` values in `settings_local.py` to match.

## Enabling WSGI under Apache2

This is basically only necessary for a production environment.

Enable WSGI within Apache2 in the usual way (there are plenty of web tutorials on this).

Edit `trove.conf` and `trove-ssl.conf` as you see fit to reflect your source
code directory and security certificates. You should probably rename 'localhost' to something
more suitable. If you do, you will also need to add that name to /etc/hosts and set 
`ALLOWED_HOST` in `settings_local.py`. If you are hosting from a subdomain, you must also set
`FORCE_SCRIPT_NAME` in `settings_local.py`. 


Then (as root) enable the sites:

  ```bash
    % cd /var/www/trove
    % cp trove.conf /etc/apache2/sites-available/
    % cp trove-ssl.conf /etc/apache2/sites-available/
    % cd /etc/apache2/sites-available/
    % apache2ctl configtest
    % a2ensite trove.conf
    % a2ensite trove-ssl.conf
    % service apache2 restart
  ```

# Running the alert listener, asynchronous tasks, and other periodic tasks

See https://github.com/SAGUARO-MMA/saguaro_tom .


# Citing this work

If you use TROVE, please cite the following:

  * [Franz et al. 2025. Optimizing Kilonova Searches: A Case Study of the Type IIb SN 2025ulz in the Localization Volume of the Low-significance Gravitational Wave Event S250818k.](https://ui.adsabs.harvard.edu/abs/2025ApJ...994L..45F/abstract)
  * [Vieira et al. 2026. Search For a Counterpart to the Subsolar Mass Gravitational Wave Candidate S251112cm.](https://ui.adsabs.harvard.edu/abs/2026arXiv260317009V/abstract)
