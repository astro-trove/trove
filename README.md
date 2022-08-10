# SAGUARO TOM #

Welcome to the saguaro_tom toolkit for GW follow-up candidate(s) searches.

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

 2. Copy env.template.sh and edit as you see fit:

  ```bash
    % cd /var/www/saguaro_tom
    % cp env.template.sh env.sh
    % vi env.sh
  ```

  3. Install dependencies:

  ```bash
    % python3 -m pip install --upgrade pip
    % python3 -m pip install -r requirements.txt
  ```

  4. Run the server:

  ```bash
    % cd /var/www/saguaro_tom
    % source env.sh `pwd` test
    % python3 manage.py runserver
  ```

## Enabling WSGI under Apache2

Enable WSGI within Apache2 in the usual way (there are plenty of web tutorials on this).

Edit `saguaro_tom.conf` and `saguaro_tom-ssl.conf` as you see fit to reflect your source
code directory and security certicates. You should probably rename 'localhost' to something
more suitable. If you do, you will also need to add that name to /etc/hosts and, possibly,
the ALLOWED_HOSTS list in `/var/www/saguaro_tom/settings.py`.


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
