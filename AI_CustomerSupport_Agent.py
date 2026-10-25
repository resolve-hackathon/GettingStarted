"""
Unified AI Customer Support Agent (Slack + Discord)
---------------------------------------------------
Runs a unified asynchronous event loop to power an AI support agent 
on both Discord and Slack simultaneously. Uses OpenAI for dynamic 
generation and a local machine learning fallback for offline intent classification.
"""

import os
import sys
import asyncio
import logging
import re

# --- Discord Imports ---
import discord
from discord.ext import commands

# --- Slack Imports ---
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

# --- AI / ML Imports ---
from openai import AsyncOpenAI

# Optional: Local ML stack for fallback categorization
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.naive_bayes import MultinomialNB
    import numpy as np
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False

# ==========================================
# 1. CONFIGURATION & LOGGING
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("AI-Support-Agent")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize Async OpenAI Client
if OPENAI_API_KEY:
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None
    logger.warning("OPENAI_API_KEY is missing. Agent will operate in local ML fallback mode only.")

# ==========================================
# 2. LOCAL ML INTENT CLASSIFIER (FALLBACK)
# ==========================================
class LocalIntentClassifier:
    """
    A basic local ML text classifier using TF-IDF and Naive Bayes to 
    categorize customer support queries if external AI APIs are missing.
    """
    def __init__(self):
        self.vectorizer = None
        self.classifier = None
        self.is_trained = False
        
        # Base training dataset for customer support topics
        self.training_data = [
            ("I can't log into my account, resetting password fails.", "account_access"),
            ("How do I change my billing address?", "billing"),
            ("My credit card was charged twice.", "billing"),
            ("The server is down and I am getting a 500 error.", "technical"),
            ("API endpoint returns timeout.", "technical"),
            ("I forgot my password.", "account_access"),
            ("Where can I download the latest version?", "general_inquiry"),
            ("Can I speak to a human please?", "escalation")
        ]
        self.train_model()

    def train_model(self):
        if not ML_AVAILABLE:
            logger.warning("Scikit-learn not installed. Local ML disabled.")
            return

        logger.info("Training offline ML intent classifier...")
        texts = [item[0] for item in self.training_data]
        labels = [item[1] for item in self.training_data]

        self.vectorizer = TfidfVectorizer(stop_words='english')
        X = self.vectorizer.fit_transform(texts)
        
        self.classifier = MultinomialNB()
        self.classifier.fit(X, labels)
        self.is_trained = True

    def predict_intent(self, text: str) -> str:
        if not self.is_trained:
            return "unknown"
        
        X_test = self.vectorizer.transform([text])
        prediction = self.classifier.predict(X_test)
        return prediction[0]

local_classifier = LocalIntentClassifier()

# ==========================================
# 3. CORE AI GENERATION ENGINE
# ==========================================
async def generate_support_response(user_query: str, platform: str) -> str:
    """
    Processes the user query via OpenAI. Falls back to local ML if unavailable.
    """
    system_prompt = (
        "You are an expert technical customer support AI agent. "
        "Provide clear, concise, and friendly solutions to user queries. "
        f"You are currently speaking to a user on {platform}. "
        "Format your answer to match the chat platform's style. "
        "If the issue requires account access or billing changes, advise them to email support@company.com."
    )

    if openai_client:
        try:
            response = await openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query}
                ],
                max_tokens=250,
                temperature=0.4
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI API error: {e}. Defaulting to offline model.")
            
    # Fallback to offline intent classification
    intent = local_classifier.predict_intent(user_query)
    fallback_responses = {
        "account_access": "I see you are having trouble accessing your account. Visit `/recover`. If you still need help, reply with 'human'.",
        "billing": "For billing or refund inquiries, please email `billing@company.com` securely.",
        "technical": "It seems you are facing a technical issue. Check our systems at `status.company.com` or attach your error logs here.",
        "general_inquiry": "You can find our full documentation at `docs.company.com`.",
        "escalation": "I am opening a ticket and connecting you with a human representative. Please hold on.",
        "unknown": "My main AI servers are unreachable, and I couldn't categorize your request. Could you simplify your question?"
    }
    
    return f"*[Fallback Offline Mode - Category: {intent}]*\n{fallback_responses.get(intent, fallback_responses['unknown'])}"


# ==========================================
# 4. DISCORD BOT SETUP
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
discord_bot = commands.Bot(command_prefix="!support ", intents=intents)

@discord_bot.event
async def on_ready():
    logger.info(f"Discord Bot online as {discord_bot.user}")

@discord_bot.event
async def on_message(message: discord.Message):
    if message.author == discord_bot.user:
        return

    # Trigger on mention or direct message
    if discord_bot.user in message.mentions or isinstance(message.channel, discord.DMChannel):
        clean_content = message.content.replace(f'<@{discord_bot.user.id}>', '').strip()
        
        async with message.channel.typing():
            response = await generate_support_response(clean_content, "Discord")
            
        await message.reply(response)
        
    await discord_bot.process_commands(message)

@discord_bot.command(name="status")
async def check_status(ctx):
    """Simple debug command for Discord"""
    await ctx.send("AI Support systems are green and operational.")


# ==========================================
# 5. SLACK BOT SETUP
# ==========================================
slack_app = AsyncApp(token=SLACK_BOT_TOKEN)

@slack_app.event("app_mention")
async def handle_slack_mentions(event, say):
    """Responds when the bot is @mentioned in a Slack channel"""
    user = event["user"]
    text = event["text"]
    
    # Remove Slack mention syntax (e.g., <@U12345>)
    clean_text = re.sub(r'<@U[A-Z0-9]+>', '', text).strip()

    response = await generate_support_response(clean_text, "Slack")
    await say(f"<@{user}>\n{response}")

@slack_app.message(re.compile(r".*", re.IGNORECASE))
async def handle_slack_dms(message, say):
    """Responds to Direct Messages in Slack"""
    if message.get("channel_type") == "im":
        user = message["user"]
        text = message["text"]
        
        response = await generate_support_response(text, "Slack")
        await say(response)


# ==========================================
# 6. UNIFIED ASYNC RUNNER
# ==========================================
async def main():
    logger.info("Booting Unified Customer Support AI...")
    tasks = []
    
    # 1. Mount Discord Task
    if DISCORD_TOKEN:
        tasks.append(discord_bot.start(DISCORD_TOKEN))
    else:
        logger.warning("DISCORD_TOKEN missing. Discord disabled.")

    # 2. Mount Slack Task
    if SLACK_BOT_TOKEN and SLACK_APP_TOKEN:
        slack_handler = AsyncSocketModeHandler(slack_app, SLACK_APP_TOKEN)
        tasks.append(slack_handler.start_async())
    else:
        logger.warning("Slack tokens missing. Slack disabled.")

    if not tasks:
        logger.error("No platform tokens found. Exiting.")
        return

    # Run both WebSocket connections inside the same asyncio loop
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Agent shut down by user.")