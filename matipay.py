import os
import sys
import json
import datetime
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram import constants as botconst
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


JSON_FILE = 'config.json'
BASE_URL  = 'https://vendingapp.matipay.com'
APP_ROOT  = '/smart-vending-webapp'

telegramcmd = {
    "oggi"         : "today",
    "ieri"         : "yesterday",
    "ultimi7gg"    : "last7",
    "ultimi30gg"   : "last30",
    "questomese"   : "thismonth",
    "mescorso"     : "lastmonth",
}

def load_config():
    with open(os.path.join(sys.path[0], JSON_FILE)) as f:
        return json.load(f)

def getMatiPayCredentials():
    conf = load_config()
    return conf['matipay']['user'], conf['matipay']['pass'], conf['matipay']['hostname']

def getBotConfig():
    conf = load_config()
    return conf['matipay_bot_config']['token_id'], conf['matipay_bot_config']['channel_id']

def getMachineNames():
    conf = load_config()
    return conf.get('matipay_machine_names', {})


def date_range(period):
    today = datetime.date.today()
    if period == 'today':
        return today, today
    elif period == 'yesterday':
        d = today - datetime.timedelta(days=1)
        return d, d
    elif period == 'last7':
        return today - datetime.timedelta(days=6), today
    elif period == 'last30':
        return today - datetime.timedelta(days=29), today
    elif period == 'thismonth':
        first = today.replace(day=1)
        return first, today
    elif period == 'lastmonth':
        last = today.replace(day=1) - datetime.timedelta(days=1)
        first = last.replace(day=1)
        return first, last
    return today, today


class MatiPayAPI:

    def __init__(self, username, password):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0'})
        self._login(username, password)

    def _login(self, username, password):
        resp = self.session.get(BASE_URL + APP_ROOT + '/login')
        soup = BeautifulSoup(resp.text, 'html.parser')
        csrf_meta = soup.find('meta', {'id': '_csrf'})
        csrf = csrf_meta['content'] if csrf_meta else ''

        resp = self.session.post(
            BASE_URL + APP_ROOT + '/j_spring_security_check',
            data={'username': username, 'password': password, '_csrf': csrf},
            allow_redirects=True,
        )
        if 'login' in resp.url:
            raise Exception('Login failed — check credentials in config.json')
        print('Logged in:', resp.url)

    def get_transactions(self, serial_number, pv_cod, date_from, date_to,
                         transaction_table='CASH'):
        """Fetch all pages of CASH transactions and return only PURCHASE (Acquisto) ones."""
        params = [
            ('vmSerialNumber',     serial_number),
            ('pvCod',              pv_cod),
            ('transactionTable',   transaction_table),
            ('transactionTimeMin', str(date_from) + ' 00:00:00'),
            ('transactionTimeMax', str(date_to)   + ' 23:59:59'),
            ('orderBy',            'TRANSACTION_TIME'),
            ('orderType',          'DESC'),
            ('page',               1),
        ]
        all_items = []

        while True:
            resp = self.session.get(
                BASE_URL + APP_ROOT + '/vm-details/transactions/list',
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get('list', [])
            all_items.extend(items)

            total = data.get('totalItems', 0)
            if len(all_items) >= total or not items:
                break
            params = [(k, v) for k, v in params if k != 'page']
            params.append(('page', len(all_items) // (data.get('pageSize') or 20) + 1))

        # Filter client-side: keep only "Acquisto - cash" (transactionType == PURCHASE)
        return [t for t in all_items if t.get('transactionType') == 'PURCHASE']

    def get_machines(self):
        """Return list of all vending machines as dicts with vmSerialNumber and pvCod."""
        all_items = []
        page = 1
        while True:
            resp = self.session.get(
                BASE_URL + APP_ROOT + '/vm/list',
                params={'page': page, 'orderBy': 'VM_SERIAL_NUMBER', 'orderType': 'ASC'},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get('list', [])
            all_items.extend(items)
            if len(all_items) >= data.get('totalItems', 0) or not items:
                break
            page += 1
        return [{'sn': m['vmSerialNumber'], 'pv': m['pvCod']} for m in all_items]

    def get_daily_total(self, serial_number, pv_cod, period='today'):
        date_from, date_to = date_range(period)
        transactions = self.get_transactions(serial_number, pv_cod, date_from, date_to)
        total = sum(float(t.get('amount', 0) or 0) for t in transactions)
        return round(total, 2), len(transactions)

    def get_all_machines_total(self, period='today'):
        """Return totals for every machine as a list of (sn, total, count)."""
        results = []
        for m in self.get_machines():
            total, count = self.get_daily_total(m['sn'], m['pv'], period)
            results.append((m['sn'], total, count))
        return results


def format_report(results, period, transaction_table='CASH'):
    label = {
        'today'    : 'Oggi',
        'yesterday': 'Ieri',
        'last7'    : 'Ultimi 7 giorni',
        'last30'   : 'Ultimi 30 giorni',
        'thismonth': 'Questo mese',
        'lastmonth': 'Mese scorso',
    }.get(period, period)

    names = getMachineNames()
    lines = ["Periodo: {}  |  Tipo: {}".format(label, transaction_table), ""]
    grand_total = 0.0
    grand_count = 0
    for sn, total, count in results:
        name = names.get(sn, sn)
        lines.append("{}  →  {} transaz.  €{:.2f}".format(name, count, total))
        grand_total += total
        grand_count += count
    lines.append("")
    lines.append("TOTALE  →  {} transaz.  €{:.2f}".format(grand_count, round(grand_total, 2)))
    return "\n".join(lines)


def end_of_month(dt):
    return (dt + datetime.timedelta(days=1)).month != dt.month


async def cmdhandler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msginput = update.message.text.replace('/', '')
    period = telegramcmd[msginput]

    user, passwd, _ = getMatiPayCredentials()
    _, channel_id = getBotConfig()
    await update.message.reply_text("Attendi, sto raccogliendo i dati...")

    api = MatiPayAPI(user, passwd)
    results = api.get_all_machines_total(period)
    msg = format_report(results, period)
    await update.message.reply_text(msg)
    await context.bot.send_message(chat_id=channel_id, text=msg)


async def callback_once(context: ContextTypes.DEFAULT_TYPE):
    _, channel_id = getBotConfig()
    user, passwd, _ = getMatiPayCredentials()
    api = MatiPayAPI(user, passwd)

    query_list = ['oggi']
    now = datetime.datetime.now()
    if now.weekday() == 6:           # Sunday
        query_list.append('ultimi7gg')
    if end_of_month(now.date()):     # Last day of month
        query_list.append('questomese')

    for key in query_list:
        period = telegramcmd[key]
        results = api.get_all_machines_total(period)
        msg = format_report(results, period)
        await context.bot.send_message(chat_id=channel_id, text=msg)


class MatiPayBot:
    def __init__(self, tokenid):
        self.app = ApplicationBuilder().token(tokenid).build()
        self.app.add_handler(CommandHandler("oggi",        cmdhandler))
        self.app.add_handler(CommandHandler("ieri",        cmdhandler))
        self.app.add_handler(CommandHandler("ultimi7gg",   cmdhandler))
        self.app.add_handler(CommandHandler("ultimi30gg",  cmdhandler))
        self.app.add_handler(CommandHandler("questomese",  cmdhandler))
        self.app.add_handler(CommandHandler("mescorso",    cmdhandler))

        self.app.job_queue.run_once(callback_once, when=5)
        self.app.run_polling()



def test():
    user, passwd, _ = getMatiPayCredentials()
    api = MatiPayAPI(user, passwd)

    for period in ('today', 'yesterday', 'last7', 'thismonth', 'lastmonth'):
        results = api.get_all_machines_total(period)
        print(format_report(results, period))
        print()


async def send_report():
    token_id, channel_id = getBotConfig()
    user, passwd, _ = getMatiPayCredentials()
    api = MatiPayAPI(user, passwd)

    periods = ['oggi', 'ieri', 'ultimi7gg']
    now = datetime.datetime.now()
    if end_of_month(now.date()):
        periods.append('questomese')

    from telegram import Bot
    bot = Bot(token=token_id)
    for key in periods:
        period = telegramcmd[key]
        results = api.get_all_machines_total(period)
        msg = format_report(results, period)
        print(msg)
        await bot.send_message(chat_id=channel_id, text=msg)


if __name__ == '__main__':
    import asyncio
    asyncio.run(send_report())
