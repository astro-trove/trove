# SAGUARO TOM #

Welcome to the saguaro-tom toolkit for GW follow-up candidate(s) searches.

## Repositories of Interest:

  * https://github.com/SAGUARO-MMA/saguaro-tom.git
  * https://github.com/SAGUARO-MMA/sassy_q3c_models.git
  * https://github.com/SAGUARO-MMA/kne-cand-vetting.git

## Installation

 1. Clone the repository:

  ```bash
    % git clone https://github.com/SAGUARO-MMA/saguaro-tom.git
  ```

 2. Copy env.template.sh and edit as you see fit:

  ```bash
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
    % source env.sh
    % python3 manage.py runserver
  ```
