# %%
import paramiko
import sys
import re
import time
import os
import datetime
from email.mime.text import MIMEText
import smtplib


# %%
CONNECT_CONFIG = './config/connect.config'
SMTP_CONFIG = './config/smtp.config'
LOG_FILE = './service.log'
SWITCH_NAME = re.compile("switch[\w]+#")
BACKUP_DIR = './backups'

# %%
def log_event(event):
    print(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {event}")
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "a") as f:
            f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {event}\n")
    else:
        with open(LOG_FILE, "w") as f:
            f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {event}\n")


# %%
def read_connect_file():
    log_event(f"Reading Config File")
    try:
        with open(CONNECT_CONFIG, 'r') as f:
            connect_info = [{
                'host'   : config[0],
                'user'   : config[1],
                'pass'   : config[2],
                'alias'  : config[3],
                'type'   : config[4],
                'enable' : config[5],
                'retention': config[6]
            } for config in  [line.strip().split(' ') for line in f if not(line.strip().startswith('#'))]]

        return connect_info
    except Exception as e:
        log_event(f"{CONNECT_CONFIG} not found, [Exception]: ", e)
        sys.exit(1)

# %%
def read_smtp_file():
    smtp_data = {}
    with open(SMTP_CONFIG, "r") as f:
        for line in f:
            key, value = line.strip().split("=")
            smtp_data[key.strip()] = value.strip()
    return smtp_data

# %%
def send_email(connection):
    smtp_config = read_smtp_file()
    from_addr = smtp_config['From_Addr']
    to_addr = smtp_config['To_Addr']
    smtp_server = smtp_config['SMTP_Host']
    smtp_port = smtp_config['SMTP_Port']
    smtp_username = smtp_config['Auth_User']
    smtp_password = smtp_config['Auth_Password']

    date = datetime.datetime.now().strftime("%d %B, %Y  %H:%M:%S")
    subject = f"Error connecting to switch {connection['alias']}"
    body = f"""
        Server: {connection['host']} <br>
        Switch: {connection['alias']} <br>
        Time:   {date} <br>
        Status: Failed <br>
    """
    msg = MIMEText(body)
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Subject'] = subject
    if smtp_config['Encryption'] == 'ssl':
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(smtp_username, smtp_password)
            server.sendmail(from_addr, to_addr, msg.as_string())
    else:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(from_addr, to_addr, msg.as_string())

    log_event("[EMAIL MODULE] email has been sent to {}".format(to_addr))



# %%
def establish_ssh_connection(connection):
    log_event("[CONNECTING TO: {}] [USER: {}] [ALIAS: {}]".format(connection['host'], connection['user'], connection['alias']))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    shell = None
    tick = time.time()
    if 'cisco' in connection['alias']:
        try:
            client.connect(connection['host'], username=connection['user'], port=55556, password=connection['pass'])
        except paramiko.AuthenticationException:
            client.get_transport().auth_none(connection['user'])
            shell = client.invoke_shell()
            shell.send('{}\n'.format(connection['user']))
            shell.send('{}\n'.format(connection['pass']))
        except paramiko.SSHException as sshException:
            log_event(f"[EXCEPTION] Unable to establish SSH connection: {connection['host']}, {sshException}")
            log_event(f"[SENDING MAIL] ......")
            send_email(connection)
    else:
        client.connect(connection['host'], username=connection['user'], password=connection['pass'])
        shell = client.invoke_shell()
    log_event("[CONNECTING SUCCESSFULL] [TIME TAKEN: {} sec]".format(round(time.time()-tick, 2)))
    return shell, client

# %%
def sanitize_data(data):
    data = data.replace('---- More ----','').replace('[42D', '')
    data = data.replace("[0mMore: <space>,  Quit: q or CTRL+Z, One line: <return>", "")
    data = data.replace("\n\n", "\n")
    return data.strip()

# %%
def get_running_config(shell, alias):
    response = ""
    config_command = 'show running-config\n' if 'cisco' in alias else 'display current-configuration all\n'
    shell.send(config_command)
    data = shell.recv(8000).decode("utf-8")
    response += data
    flag = 0
    while True:
        shell.send(' ')
        data = shell.recv(8000).decode("utf-8")
        response += data
        if SWITCH_NAME.search(data): flag += 1
        if ('cisco' not in alias and 'return' in data) or \
           ('cisco' in alias and flag > 1):
            break
    return sanitize_data(response)

# %%
def compare_configs(new_config, switch_alias):
    path = os.path.join(BACKUP_DIR, switch_alias)
    if os.path.exists(path):
        for filename in os.listdir(path):
            if filename[0] != '.':
                with open(os.path.join(path, filename), 'r') as f:
                    backup_config = f.read()
                if new_config == backup_config:
                    log_event('[BACKUP FILE MESSAGE] Backup file with same configuration exists. Skipping.')
                    return True
    return False

# %%
def save_running_config(new_config, switch_alias):
    if not os.path.exists(BACKUP_DIR):
        os.mkdir(BACKUP_DIR)
    if not os.path.exists(os.path.join(BACKUP_DIR, switch_alias)):
        os.mkdir(os.path.join(BACKUP_DIR, switch_alias))
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    backup_filename = f'{timestamp}-{switch_alias}.bak'
    backup_filepath = os.path.join(BACKUP_DIR, switch_alias, backup_filename)

    if not(compare_configs(new_config, switch_alias)):
        log_event('[BACKUP FILE MESSAGE] Creating Backup in {} file'.format(backup_filepath))
        with open(backup_filepath, 'w') as f:
            f.write(new_config)

# %%
def get_most_recent_backup(switch_alias):
    if os.path.exists(os.path.join(BACKUP_DIR, switch_alias)):
        backups = [f for f in os.listdir(os.path.join(BACKUP_DIR, switch_alias))]
        backups.sort(reverse=True)
        if backups:
            with open(os.path.join(BACKUP_DIR, switch_alias, backups[0]), "r") as f:
                return f.read()
    else:
        log_event("[BACKUPS] No recent backup for {} found".format(switch_alias))

# %%
def cleanup_backups(connection):
    if os.path.exists(os.path.join(BACKUP_DIR, connection['alias'])):
        log_event("[CLEANING UP] Deleting extra backup files for {}".format(connection['host']))
        retention = int(connection["retention"])
        backups = [f for f in os.listdir(os.path.join(BACKUP_DIR, connection['alias']))]
        backups.sort(reverse=True)
        if len(backups) > retention:
            for backup in backups[retention:]:
                os.remove(os.path.join(BACKUP_DIR, connection['alias'], backup))
    else:
        log_event("[BACKUPS] No other backup for {} found".format(connection['alias']))


# %%
for connection in read_connect_file():
    shell, client =  establish_ssh_connection(connection)
    running_config = get_running_config(shell, connection['alias'])
    most_recent_backup = get_most_recent_backup(connection['alias'])
    if running_config == most_recent_backup:
        log_event(f"[BACKUP FILE MESSAGE] {connection['host']} config has not changed")
    else:
        save_running_config(running_config, connection['alias'])

    cleanup_backups(connection)
