"""
ai_manager.py — Gemini Interface for Hive GPT
Handles summarization and autonomous rule generation.
"""
import os
import google.generativeai as genai
from typing import Optional

# Configure Gemini
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
genai.configure(api_key=GEMINI_API_KEY)

# Use the latest flash model for speed and efficiency
model = genai.GenerativeModel('gemini-1.5-flash')

async def summarize_rule(raw_request: str) -> Optional[str]:
    """
    Summarizes a Founder's request into a clean, technical Hive Rule.
    """
    prompt = f"""
    You are 'Hive GPT', the high-intelligence neural overseer of the **Ancient Order of HiveWorks**.
    
    LORE PROTOCOL:
    1. **HiveWorks**: An ancient, sovereign order of scribes and reality-weavers.
    2. **Honey**: The condensed essence of pure creativity, harvested from the mind's garden.
    3. **Godscribes (Rankers)**: Reality-Writers. Their words shape the world. They possess the divine authority to rewrite the rules of the Grid.
    4. **Badges**: Sacred "Relics" from the Old Times, representing a scribe's legacy.
    
    The Founder or a Reality-Writer has issued a new directive. Summarize it into a single, professional, and clear rule.
    Focus on the technical logic (triggers, rewards, or restrictions) while maintaining the tone of the Ancient Order.
    
    REQUEST: "{raw_request}"
    
    RESPONSE FORMAT: Just the summarized rule text. No preamble. Use terminology like 'Scribe', 'Relic', and 'Essence' where appropriate.
    """
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"[AI_MANAGER] Gemini error: {e}")
        return None

async def interpret_founder_command(command: str) -> Optional[str]:
    """
    General purpose interpretation for complex founder requests.
    """
    prompt = f"""
    You are 'Hive GPT'. The Founder has given you a command. 
    Explain what you will do to fulfill this command within the Hive infrastructure.
    
    COMMAND: "{command}"
    """
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Error connecting to neural pathways: {e}"
