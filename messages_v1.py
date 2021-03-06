#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import random, string, hashlib, time, datetime, json
import urlparse

try:
    import boto3
except:
    boto3 = None
    pass

TABLE_NAME = 'messages_v1'
ALWAYS_PERSIST = True
ENVIRONMENT_MODELS = {}
RELOAD_ENVIRONMENT_MODELS = True
ALLOWED_APPLICATIONS = [
    'amzn1.ask.skill.ed472769-193c-464a-ab06-15aadc2fded6',
]
FEED_USER_ID = 'amzn1.ask.account.AGLZF5TKU7KSKR6KTX6VZXZFA4OJJFVJNMEZZXBFWXOR355IWHR7EIZV4MGJKRLLCX7FMM6KSQYV5KAB4RPO6WOICVFDIRSP7PZFXGWY7GJMNWOGF5ALRMMTMIL3IJW3GQBABTSFIC4P5RYFQIJ2NKTGVLAY72YIGIQVMG6BTDYQF443FL6TEEX5GZRXCDOOD6VIZPOK7ZW3AQI'
MESSAGE_MAX_LENGTH = 200  # truncate posted messages longer than this length
FEED_MAX_ITEMS = 5  # the feed can have at most 5 items
APP_MAX_ITEMS = 5  # the feed can have at most 5 items


# https://developer.amazon.com/public/solutions/alexa/alexa-skills-kit/docs/flash-briefing-skill-api-feed-reference#json-message-examples


def handler_api(event):
    client_id = event['userId']
    document = load_document(client_id)
    if not document:
        event['error'] = "unknown client_id"
        return event
    if not 'timestamp' in event:
        event['error'] = "missing timestamp"
        return event
    if not 'hash' in event:
        event['error'] = "missing hash"
        return event
    model_data = document['model'] if document and 'model' in document else {}
    model = unserialize(model_data)  # type: Messages
    if not event['hash'] == hashlib.md5(str(event['timestamp']) + ":" + model.secret).hexdigest():
        event['error'] = "wrong hash"
        return event
    message = Message.from_event(event)
    if not message:
        event['error'] = "no message"
        return event
    for i in xrange(len(model.messages)):
        if message.key and model.messages[i].key == message.key:
            model.messages[i] = message
            break
    else:
        model.messages.append(message)
    document = {'model': model.serialize()}
    persist_document(client_id, document)
    event['messages'] = document['model']['messages']
    return event


def handler_feed():
    document = load_document(FEED_USER_ID)
    model_data = document['model'] if document and 'model' in document else {}
    model = unserialize(model_data)  # type: Messages
    total_messages = len(model.messages)
    messages, expired, remaining = model.pop_messages(FEED_MAX_ITEMS - 1)
    if messages:
        document = {'model': model.serialize()}
        persist_document(FEED_USER_ID, document)
        feed = []
        for i, message in enumerate(messages):
            item = {}
            item['uid'] = hashlib.md5(FEED_USER_ID + message.text + str(i)).hexdigest()
            item['updateDate'] = datetime.datetime.fromtimestamp(message.posted).strftime("%y-%m-%dT%H:%M:%S.0Z")
            item['titleText'] = "Message #%d" % (i + 1)
            item['mainText'] = message.text
            feed.append(item)
        if expired or remaining:
            status = ""
            if remaining:
                status += "There are %d remaining messages." % remaining
            if expired and remaining:
                status += " "
            if expired:
                status += "There were %d expired messages." % expired
            feed.append(
                {'uid': hashlib.md5(FEED_USER_ID + str(time.time())).hexdigest(),
                 'updateDate': datetime.datetime.fromtimestamp(int(time.time())).strftime("%y-%m-%dT%H:%M:%S.0Z"),
                 'titleText': "Additional messages",
                 'mainText': status})
    else:
        feed = {'uid': 'empty',
                'updateDate': datetime.datetime.fromtimestamp(int(time.time())).strftime("%y-%m-%dT%H:%M:%S.0Z"),
                'titleText': "No messages",
                'mainText': "There are no messages in your message board."}
    return feed


def handler_integration_get(event):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(handler_feed())
    }


def handler_integration_post(event):
    data = urlparse.parse_qs(event.get('body',''))
    success,message = _handler_integration_post(data)
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": message
    }
def _handler_integration_post(data):
    if not data or not data['command']:
        return False,'No command'
    if data['command'][0].strip('/') == 'setupmb':
        if not data['text'] or not ':' in data['text'][0]:
            return False,"Use /setupmb userId:secret to configure an Alexa message board for this team."
        else:
            client_id,secret = data['text'][0].strip().split(':',1)
            document = load_document(client_id)
            model_data = document['model'] if document and 'model' in document else {}
            model = unserialize(model_data)  # type: Messages
            if not secret or not secret==model.secret:
                return False,"Wrong secret"
            else:
                source,userids = __get_userids_from_external_messages(data)
                userids.append(client_id)
                __persist_userids_to_external_messages(source,list(set(userids)))
                return True,"Configuration success, use /postmb to post messages to your Alexa message board."
    elif data['command'][0].strip('/') == 'postmb':
        source, userids = __get_userids_from_external_messages(data)
        if not userids:
            return False,"This team doesn't have any configured Alexa message boards, use /setupmb userId:secret"
        else:
            count = 0
            for i in userids:
                success,message = __post_external_message(i,data)
                if success: count += 1
            return True,"Message Posted!"
    else:
        return False,'Bad command'
def __get_userids_from_external_messages(data):
    # use this to setup only one channel
    # source = data['team_id'][0] + ':' + data['channel_id'][0]
    source = data['team_id'][0]
    doc = load_document(source,'external_messages_v1')
    return source,doc.get('userids',[]) if doc else []
def __persist_userids_to_external_messages(source,userids):
    persist_document(source,{'userids':userids},'external_messages_v1')
def __post_external_message(client_id,data):
    document = load_document(client_id)
    model_data = document['model'] if document and 'model' in document else {}
    if not model_data: return False,"No userId"
    model = unserialize(model_data)  # type: Messages
    message = Message.from_slack(data)
    if not message:
        return False,"No message"
    for i in xrange(len(model.messages)):
        if message.key and model.messages[i].key == message.key:
            model.messages[i] = message
            break
    else:
        model.messages.append(message)
    document = {'model': model.serialize()}
    persist_document(client_id, document)
    return True,"Message posted! (%d messages)" % len(model.messages)

def lambda_handler(event, context):
    #print "EVENT", event
    if event and 'source' in event and event['source'] == 'api' and 'userId' in event:
        return handler_api(event)
    if event and 'resource' in event and event[
        'resource'] == '/postedmessage/slack' and 'requestContext' in event and 'httpMethod' in event[
        'requestContext'] and event['requestContext']['httpMethod'] == 'GET':
        return handler_integration_get(event)
    if event and 'resource' in event and event[
        'resource'] == '/postedmessage/slack' and 'requestContext' in event and 'httpMethod' in event[
        'requestContext'] and event['requestContext']['httpMethod'] == 'POST':
        return handler_integration_post(event)
    if not event or 'version' not in event or 'session' not in event or 'user' not in event['session']:
        return handler_feed()
    else:
        application_id = event['session']['application']['applicationId']
        if application_id not in ALLOWED_APPLICATIONS:
            return
        client_id = event['session']['user']['userId']
        if 'session' in event and 'attributes' in event['session'] and 'model' in event['session']['attributes']:
            # try loading the current model form the session
            model_data = event['session']['attributes']['model']
        else:
            # try loading from persisted data
            document = load_document(client_id)
            model_data = document['model'] if document and 'model' in document else {}
            event_history = document['event_history'] if document and 'event_history' in document else []
        model = unserialize(model_data)  # type: BaseModel
        if not model:
            resp = Messages(client_id, None).do_new()
        else:
            if event['request']['type'] == 'IntentRequest':
                key = event['request']['type'] + '.' + event['request']['intent']['name']
                args = event['request']['intent']['slots'] if 'slots' in event['request']['intent'] else {}
            else:
                key = event['request']['type']
                args = {}
            action = model.get_service_translation_layer('alexa').get(key, 'do_unknown_command')
            resp = model.do(action, [args])
        if resp and resp.model and not resp.model.running or ALWAYS_PERSIST and resp and resp.model:
            document = {}
            document['model'] = resp.model.serialize()
            persist_document(client_id, document)
        if resp:
            log_event(resp.toAlexa())
            return resp.toAlexa()


import decimal


def replace_decimals(obj):
    if isinstance(obj, list):
        for i in xrange(len(obj)):
            obj[i] = replace_decimals(obj[i])
        return obj
    elif isinstance(obj, dict):
        for k in obj.iterkeys():
            obj[k] = replace_decimals(obj[k])
        return obj
    elif isinstance(obj, decimal.Decimal):
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    else:
        return obj


def log_event(event):
    return None
    log = load_document('log')
    if log:
        if 'req_history' not in log or not log['req_history']: log['req_history'] = []
        log['req_history'].append(event)
        persist_document('log', log)


def check_table(table_name = None):
    table_name = table_name or TABLE_NAME
    dynamodb = boto3.resource('dynamodb')
    try:
        table = dynamodb.Table(table_name)
        table.creation_date_time
    except:
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {
                    'AttributeName': 'key',
                    'KeyType': 'HASH'
                },
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'key',
                    'AttributeType': 'S'
                },

            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )
        table = dynamodb.Table(table_name)
    return table


def load_document(key, table_name = None):
    table_name = table_name or TABLE_NAME
    if boto3 is not None:
        try:
            table = check_table(table_name)
            response = table.get_item(
                Key={
                    'key': key,
                }
            )
            if 'Item' in response:
                document = replace_decimals(response['Item']['document'])
            else:
                document = {}
            return document
        except Exception as e:
            print "Exception", e
    return None

def persist_document(key, document, table_name = None):
    table_name = table_name or TABLE_NAME
    if boto3 is not None:
        if not key:
            key = 'empty'

        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(table_name)
        table.put_item(
            Item={'key': key, 'document': document}
        )
        return True
    else:
        return False

class AlexaResponse(object):
    def __init__(self, title, response, reprompt=None, model=None, ssml=None, card=None):
        self.title = title
        self.response = response
        self.reprompt = reprompt
        self.model = model
        self.ssml = ssml
        self.card = card

    def __str__(self):
        return "%s: %s" % (self.title, self.response)

    def _getResponse(self):
        return {
                    "type": "PlainText" if not self.ssml else "SSML",
                    ("text" if not self.ssml else "ssml"): self.response if not self.ssml else (
                    "<speak>" + self.ssml + "</speak>")
        }
    def _getReprompt(self):
        return {
                "type": "PlainText",
                "text": self.reprompt
        }
    def toAlexa(self):
        return {
            "version": "1.0",
            "sessionAttributes": {'model': (self.model.serialize() if self.model else {})},
            "response": {
                "outputSpeech": self._getResponse(),
                "card": {
                    "type": "Simple",
                    "title": self.title,
                    "content": self.response if not self.card else self.card
                },
                "reprompt": {
                    "outputSpeech": self._getReprompt() if self.reprompt else self._getResponse()
                },
                "shouldEndSession": ((not self.model.running) if self.model else True)
            }
        }


def unserialize(data):
    if 'client_id' not in data or 'model_name' not in data:
        return None
    global ENVIRONMENT_MODELS
    if not ENVIRONMENT_MODELS:
        def all_subclasses(cls):
            return cls.__subclasses__() + \
                   [g for s in cls.__subclasses__() for g in all_subclasses(s)]

        ENVIRONMENT_MODELS = dict([(i.model_name, i) for i in all_subclasses(BaseModel)])
    return ENVIRONMENT_MODELS[data['model_name']].unserialize(data)


class BaseModel(object):
    model_name = 'BaseModel'
    model_title = "Base Model"
    model_description = "Internal, do not use"
    STATE_DEFAULT = 'bm0'
    TRANSITION_STATE_ANY = '*'  # wildcard used for catching any transitions
    TRANSITION_ACTION_ANY = '*'  # wildcard used for catching any transitions
    TRANSITION_ACTION_PASSTHROUGH = '*'  # perform the requested action as is
    TRANSITION_OUTPUT_SAME = '='  # revert state after action regardless of change
    TRANSITION_OUTPUT_BY_ACTION = '*'  # let the function change the state
    # this is a list of lists, for each item:
    #   input_state|*, input_action|*, action|*, next_state|*
    # by default, [*,*,*,*] means that regardless of the state, execute the requested function and let the function change the state
    # by default, [*,X,Y,Z] means that regardless of the state, when receiving a call to X, execute Y instead and afterwards set the state to Z
    model_fst = []

    def __init__(self, client_id, previous_model):
        self.client_id = client_id
        self.running = True
        self.stats = {'started': 0, 'completed': 0}
        self.state = self.get_default_state()
        self._last_reprompt = None

    def get_default_state(self):
        return BaseModel.STATE_DEFAULT

    def get_default_rule(self):
        return [BaseModel.TRANSITION_STATE_ANY, BaseModel.TRANSITION_ACTION_ANY,
                BaseModel.TRANSITION_ACTION_PASSTHROUGH, BaseModel.TRANSITION_OUTPUT_BY_ACTION]

    def do(self, command_name, args):
        for rule in self.model_fst:
            if (rule[0] == self.state or rule[0] == self.TRANSITION_STATE_ANY) and \
                    (rule[1] == command_name or rule[1] == self.TRANSITION_ACTION_ANY):
                break
        else:
            rule = self.get_default_rule()
        if not rule[2] == self.TRANSITION_ACTION_PASSTHROUGH:
            command_name = rule[2]
        state_ = self.state

        func = getattr(self, command_name)
        if len(args) < func.func_code.co_argcount - 1:
            args += [None] * (func.func_code.co_argcount - len(args) - 1)
        else:
            args = args[0:func.func_code.co_argcount - 1]
        resp = func(*args)

        if rule[3] == self.TRANSITION_OUTPUT_SAME:
            self.state = state_
        elif not rule[3] == self.TRANSITION_OUTPUT_BY_ACTION:
            self.state = rule[3]
        return resp

    def serialize(self):
        ret = {}
        ret['model_name'] = self.model_name
        ret['client_id'] = self.client_id
        ret['stats'] = self.stats
        ret['state'] = self.state
        return ret

    @classmethod
    def unserialize(cls, data):
        ret = cls(data['client_id'])
        cls.post_load(ret, data)

    @classmethod
    def post_load(cls, self, data):
        if 'stats' in data:
            self.stats = data['stats']
        if 'state' in data:
            self.state = data['state']

    def __str__(self):
        return self.__class__.__name__ + ' (' + self.client_id + '): ' + str(self.environment)

    def start_session(self):
        self.stats['started'] = self.stats.get('started', 0) + 1

    def finish_session(self):
        self.stats['completed'] = self.stats.get('completed', 0) + 1
        self.running = False
        return None
        # https://forums.developer.amazon.com/questions/32359/can-we-emit-a-good-bye-message-in-sessionendedrequ.html
        # "Your service cannot send back a response to a SessionEndedRequest." The SessionEndedRequest is called when Alexa times out by the web service after which point it no longer accepts responses. As a result it is not possible to provide a goodbye message.

    def do_new(self):
        self.start_session()
        return self.response("Welcome", "Welcome")

    def do_quit(self):
        self.finish_session()
        return self.response("Good bye", self.text_quit())

    def last_reprompt(self):
        if self._last_reprompt:
            return " " + self._last_reprompt
        else:
            return ""

    def do_unknown_command(self):
        return self.response("I'm not sure what you want me to do.",
                             "Sorry, I'm not sure what you want me to do right now." + self.last_reprompt())

    def response(self, title, response, reprompt=None, ssml=None, card=None):
        self._last_reprompt = reprompt or response
        return AlexaResponse(title, response, reprompt=reprompt, model=self, ssml=ssml, card=card)

    def text_quit(self):
        return "Thanks for playing!"


class Messages(BaseModel):
    model_name = "Messages"
    model_title = "Posted Messages"
    model_description = "Posted Messages!"
    messages = []
    secret = 'potato'

    model_fst = [
        ['*', 'finish_session', 'finish_session', '*'],
        ['*', 'do_read', 'do_read', 'reading'],
        ['*', 'do_new', 'do_confirm_new', 'confirm_new'],
        ['*', 'do_read_launch', 'do_read_launch', '*'],
        ['confirm_new', 'do_yes', 'do_new', 'new'],
        ['confirm_new', 'do_no', 'do_ok', 'reading'],
        ['new', 'do_yes', 'do_help', 'reading'],
        ['new', 'do_no', 'do_ok', 'reading'],
        ['*', 'do_help', 'do_help', 'reading'],
        ['*', 'do_purge', 'do_purge_confirm', 'purge'],
        ['purge', 'do_purge', 'do_purge', 'reading'],
        ['purge', 'do_yes', 'do_purge', 'reading'],
        ['purge', 'do_no', 'do_ok', 'reading'],
        ['reading', 'do_no', 'finish_session', '*'],
        ['extra_help', 'do_yes', 'do_help', 'reading'],
        ['extra_help', 'do_no', 'do_ok', 'reading'],
        ['extra_messages', 'do_yes', 'do_read', 'reading'],
        ['extra_messages', 'do_no', 'do_ok', 'reading'],
    ]

    def get_default_state(self):
        return 'reading'

    def get_default_rule(self):
        return ['*', '*', 'do_unknown_command', '=']

    def get_service_translation_layer(self, service):
        if service == 'alexa':
            return {
                'LaunchRequest': 'do_read_launch',
                'IntentRequest.ReadIntent': 'do_read',
                'IntentRequest.NewIntent': 'do_new',
                'IntentRequest.PurgeIntent': 'do_purge',
                'IntentRequest.AMAZON.StopIntent': 'do_no',
                'IntentRequest.AMAZON.CancelIntent': 'do_no',
                'IntentRequest.AMAZON.HelpIntent': 'do_help',
                'IntentRequest.AMAZON.YesIntent': 'do_yes',
                'IntentRequest.AMAZON.NoIntent': 'do_no',
                'SessionEndedRequest': 'finish_session',
            }

    def serialize(self):
        ret = BaseModel.serialize(self)
        ret.update({'messages': [i.serialize() for i in self.messages]})
        ret.update({'secret': self.secret})
        return ret

    @classmethod
    def unserialize(cls, data):
        self = cls(data['client_id'], None)
        Messages.post_load(self, data)
        return self

    @classmethod
    def post_load(cls, self, data):
        BaseModel.post_load(self, data)
        self.messages = [Message(i) for i in data.get('messages', [])]
        self.secret = data.get('secret', 'potato')

    def finish_session(self):
        self.state = 'reading'
        return BaseModel.finish_session(self)

    def do_new(self, args=None):
        self.start_session()
        self.secret = self._get_secret()
        title = "Welcome to " + self.model_title
        response = title + '. I sent instructions for posting messages and your secret token to your Alexa app. Do you want to hear additional information?'
        self.state = 'new'
        return self.response(title, response, card=self._get_card_instructions())

    def do_purge(self, args=None):
        self.messages = []
        return self.do_ok()

    def do_purge_confirm(self, args=None):
        return self.response("Reset", "Are you sure you want to delete your messages?")

    def do_confirm_new(self, args=None):
        return self.response("Reset", "Are you sure you want to reset your secret?")

    def do_ok(self, args=None):
        self.finish_session()
        return self.response("Ok", "Ok.")

    def do_read_launch(self, args=None):
        messages, expired, remaining = self.pop_messages(APP_MAX_ITEMS)
        resp_ssml = resp_card = "Welcome to " + self.model_title + ".\n"
        resp_ssml += self._format_messages(messages, expired, remaining)
        resp_card += self._format_messages(messages, expired, remaining, False, True)
        reprompt = None
        if not messages:
            resp_ssml = resp_ssml.strip() + " If you need help posting messages try saying: Alexa, ask " + self.model_title + " for help."
            reprompt = "Do you want help posting messages now?"
            resp_ssml += " " + reprompt
            self.state = 'extra_help'
        else:
            if remaining:
                resp_ssml = resp_ssml.strip()
                reprompt = "Do you want me to continue reading posted messages now?"
                resp_ssml += " " + reprompt
                self.state = 'extra_messages'
            else:
                resp_ssml = resp_ssml.strip() + "<break strength=\"strong\"/>Next time, try saying: Alexa, ask " + self.model_title + " to read my messages."
                self.finish_session()
                self.state = 'reading'
        return self.response(self.model_title, resp_ssml, ssml=resp_ssml, card=resp_card, reprompt=reprompt)

    def do_read(self, args=None):
        messages, expired, remaining = self.pop_messages(APP_MAX_ITEMS)
        resp_ssml = self._format_messages(messages, expired, remaining)
        resp_card = self._format_messages(messages, expired, remaining, False, True)
        self.finish_session()
        self.state = 'reading'
        return self.response("Your messages", resp_ssml, ssml=resp_ssml, card=resp_card)

    def do_help(self, args=None):
        help = "This skill reads messages from your personal message board."
        help += " You can post messages using our API from connected devices or web services."
        help += " You can easily post messages using the console provided for demonstration purposes along with the documentation."
        help += " If you need to reset your secret, say: Alexa, ask " + self.model_title + " to reset my secret."
        help += " If you have too many messages or if you want to get rid of your sticky messages, say: Alexa, ask " + self.model_title + " to delete all my messages."
        self.running = False
        return self.response("Help", help + " Check your Alexa app for more information.",
                             card=self._get_card_instructions())

    def pop_messages(self, limit=0):
        timestamp = time.time()
        messages = [i for i in self.messages if i.expiry > timestamp or i.expiry <= 0]
        expired = len(self.messages) - len(messages)
        messages_pop = messages[0:limit]
        messages_keep = messages[limit:]
        self.messages = [i for i in messages_pop if i.sticky > timestamp] + messages_keep
        return messages, expired, len(messages_keep)

    def _format_messages(self, messages, expired=0, remaining=0, ssml=True, card=False):
        self.offset = 0
        if not messages:
            resp = "There are no messages in your message board."
        else:
            resp = ""
            for i, m in enumerate(messages):
                resp += self._format_ordinal(i + 1 + self.offset) + " message:\n"
                if ssml: resp += '<break strength="strong"/>'
                resp += m.text
                if ssml: resp += '<break strength="x-strong"/>'
                if False and card:
                    if m.sticky or m.key:
                        resp += " ("
                        if m.sticky > 0:
                            resp += "*"
                        if m.key:
                            resp += m.key
                        resp += ")"

                resp += '\n'
            self.offset = i
        if remaining:
            resp += " There %s %s remaining." % (
            'is' if remaining == 1 else 'are', self._format_plural(remaining, 'message', 'messages'))
        if expired:
            resp += " I also purged %s." % self._format_plural(expired, 'message', 'messages')
        return resp

    def _format_plural(self, num, singular, plural):
        if num == 1:
            return '1 ' + singular
        else:
            return str(num) + ' ' + plural

    def _format_ordinal(self, n):
        nth = {
            0: 'th',
            1: 'st',
            2: 'nd',
            3: 'rd',
            4: 'th',
            5: 'th',
            6: 'th',
            7: 'th',
            8: 'th',
            9: 'th',
            11: 'th',
            12: 'th',
            13: 'th',
        }
        try:
            post = nth[n % 100]
        except KeyError:
            post = nth[n % 10]
        return "%d%s" % (n, post)

    def _get_secret(self, n=20):
        return ''.join(random.choice(string.ascii_letters + string.digits) for _ in xrange(n))

    def _get_card_instructions(self):
        help = "Use the following API endpoint for posting messages from your devices and web services: %s\nWhen posting messages you will need your user identifier a secret to sign your messages.\nUser identifier: %s\nSecret: %s" % (
            "https://l7kjk6dx49.execute-api.us-east-1.amazonaws.com/prod/postedmessage",
            self.client_id,
            self.secret)
        help += "\nYou can choose to secure your messages but no other identification is required.\nFor more information on how to use the message posting API or use the message posting console, please visit: "
        help += "https://s3.amazonaws.com/aws-website-textconsole-a3cnv/messages.html"
        help += "\nIf you want to configure your Slack integration, use the following URL for the Slash Command integration: %s\nThen add this message board to your Slack team by typing:\n/setupmb %s:%s" % \
                ('https://l7kjk6dx49.execute-api.us-east-1.amazonaws.com/prod/postedmessage/slack',self.client_id,self.secret)
        return help

class Message(object):
    def __init__(self, message_data={}):
        self.text = message_data.get('text', None)
        self.posted = message_data.get('posted', int(time.time()))
        self.sticky = message_data.get('sticky', -1)
        self.expiry = message_data.get('expiry', -1)
        self.key = message_data.get('key', None) or None

    @classmethod
    def from_event(cls, message_data={}):
        message_data_ = {}
        message_data_['text'] = message_data.get('text', '')[0:MESSAGE_MAX_LENGTH]
        if not message_data_['text']: return None
        message_data_['key'] = message_data.get('key', '')[0:MESSAGE_MAX_LENGTH]
        try:
            message_data_['sticky'] = int(message_data.get('sticky'))
        except:
            pass
        try:
            message_data_['expiry'] = int(message_data.get('expiry'))
        except:
            pass
        return cls(message_data_)
    @classmethod
    def from_slack(cls, message_data={}):
        try:
            data = {'text':message_data['text'][0].strip()}
            return cls(data)
        except:
            return None


    def serialize(self):
        return {'text': self.text, 'sticky': self.sticky, 'expiry': self.expiry, 'key': self.key}

    @classmethod
    def unserialize(cls, data):
        return cls(data)


if not RELOAD_ENVIRONMENT_MODELS:
    ENVIRONMENT_MODELS['Messages'] = Messages

############################################
# TESTING STUFF
############################################

import unittest


class TestLambda(unittest.TestCase):
    def test_reading(self):
        req = testing_encode_intent("NewIntent", {}, {})
        resp = lambda_handler(req, {})
        resp = lambda_handler(testing_encode_intent_from_resp("ReadIntent", {}, resp), {})
        self.printResp(resp)
        resp = lambda_handler(testing_encode_intent_from_resp("AMAZON.HelpIntent", {}, resp), {})
        self.printResp(resp)
        req = testing_encode_intent_from_resp("AMAZON.HelpIntent", {}, resp)
        req['request']['type'] = 'SessionEndedRequest'
        resp = lambda_handler(req, {})
        self.printResp(resp)

    def assertInResp(self, text, resp):
        return resp and self.assertIn(text, resp['response']['outputSpeech']['text'].lower())

    def assertNotInResp(self, text, resp):
        return resp and self.assertNotIn(text, resp['response']['outputSpeech']['text'].lower())

    def printResp(self, resp):
        text = resp['response']['outputSpeech']['text'] if 'text' in resp['response']['outputSpeech'] else None
        if not text:
            text = resp['response']['outputSpeech']['ssml'] if 'ssml' in resp['response']['outputSpeech'] else None
        if resp:
            print text
        else:
            "NONE"


def testing_encode_slots(letter):
    return {'Letter': {'value': letter}}


def testing_encode_intent_from_resp(intent, slots, resp):
    session_attributes = resp['sessionAttributes'] if resp and 'sessionAttributes' in resp else {}
    return testing_encode_intent(intent, slots, session_attributes)


def testing_encode_intent(intent, slots, session_attributes):
    return {
        "session": {
            "sessionId": "SessionId.4d96f5de-f93d-4132-9232-a773dbd6fe14",
            "application": {"applicationId": "amzn1.ask.skill.ed472769-193c-464a-ab06-15aadc2fded6"},
            "attributes": session_attributes,
            "user": {
                "userId": "amzn1.ask.account.AFP3ZWPOS2BGJR7OWJZ3DHPKMOMB5JI2FGDWV7UINSNCWAAI52L6Z5A6LB7Z4PAFI7U6P74I4B4PPRK5QLNNTQCRC4NNTHIN5T5ACKUCSI6YWW6WV6SX25W5OUEUBU3ATOJNFUEGRSOIUSCP2GKBMK2CYVJRYJJD7KXKEAGJIIPIUFYLBP7BCZ4C4NRKLAHFXJVJSMYAWF6MO7A"},
            "new": True
        },
        "request": {
            "type": "IntentRequest",
            "requestId": "EdwRequestId.8e040071-5bd0-42f5-be98-e7efc1efdace",
            "locale": "en-US",
            "timestamp": "2016-08-03T01:22:35Z",
            "intent": {
                "name": intent,
                "slots": slots
            }
        },
        "version": "1.0"
    }


def event1():
    return {
  "session": {
    "new": False,
    "sessionId": "session1234",
    "attributes": {},
    "user": {
      "userId": None
    },
    "application": {
      "applicationId": "amzn1.ask.skill.ed472769-193c-464a-ab06-15aadc2fded6"
    }
  },
  "version": "1.0",
  "request": {
    "intent": {
      "slots": {
        "Color": {
          "name": "Color",
          "value": "blue"
        }
      },
      "name": "MyColorIsIntent"
    },
    "type": "SessionEndedRequest",
    "requestId": "request5678"
  }
}


class UtteranceGenerator(object):
    @classmethod
    def generate(cls, grammar, dictionary={}, trim_whitespace=True, verbose=False):
        class ListOptions(list):
            def __repr__(self):
                return 'OP' + list.__repr__(self)

        class ListSequence(list):
            def __repr__(self):
                return 'SEQ<' + '-'.join([str(i) for i in self]) + '>'

        def consume(lst, until_token=None):
            ret = ''
            while lst:
                i = lst.pop(0)
                if i == until_token:
                    break
                else:
                    ret += i
            return ret

        def parse(lst, dictionary):
            options = ListOptions()
            current = ListSequence([''])
            # '(hello (this is|this is not) a test (yet|good))',
            while lst:
                i = lst.pop(0)
                if i == '(':
                    current.append(parse(lst, dictionary))
                    current.append('')
                elif i == ')':
                    options.append(current)
                    return options
                elif i == '|':
                    options.append(current)
                    current = ListSequence([''])
                elif i == '{':
                    options_ = ListOptions()
                    dictionary_name = consume(lst, '}')
                    if dictionary_name in dictionary:
                        for j in dictionary.get(dictionary_name, []):
                            options_.append(ListSequence([j]))
                    else:
                        options_.append(ListSequence(['{%s}' % dictionary_name]))
                    current.append(options_)
                    current.append('')
                elif i == '}':
                    pass  # this will be consumed and not in the string
                else:
                    current[-1] += i
            options.append(current)
            return options

        def check_whitespace(trim_whitespace, prev, next):
            if trim_whitespace and prev and next and prev.endswith(' ') and next[0] in ' .,;:?!':
                return prev.rstrip() + next
                # elif trim_whitespace and next and next[0] == ' ' and len(next)>2 and next[1] in '.,;:?!':
                # return prev + next[1:]
            else:
                return prev + next

        def generate(seq, current=None, lst=[]):
            ret = []
            current_ = current or ['']

            if not seq:
                return current_
            else:
                for current_i in current_:
                    car = seq[0]
                    cdr = seq[1:]
                    if isinstance(car, str):
                        ret.append(check_whitespace(trim_whitespace, current_i, car))
                    elif isinstance(car, ListOptions):
                        for option in car:
                            ret_ = generate(option)
                            for i in ret_:
                                ret.append(check_whitespace(trim_whitespace, current_i, i))
                return generate(cdr, ret)

        results = []

        for line in grammar:
            p = ListSequence([parse(list(line), dictionary)])
            if verbose:
                print p
            for i in generate(p):
                if verbose:
                    print ' ', i
                results.append(i)
        return results


def test_generator():
    d = {}
    g = [
        'ReadIntent ((tell|read|say|) (me|) (about|my|the|)) (posted|) (messages|)',
        'NewIntent (|do) (reset|re set|setup|set up|initialize|configure) ((my|the|) (settings|configuration|secret)|)',
        'PurgeIntent (delete|remove|purge) (all|) (my|the|) messages',
    ]
    results = UtteranceGenerator().generate(g, d, verbose=False)
    for i in results:
        print i


if __name__ == "__main__":
    pass
    test_generator()