from botasaurus.browser import browser, Driver

import time
import google.generativeai as genai
import os
from dotenv import load_dotenv
import mutagen
import requests
import json
import base64
from datetime import datetime

load_dotenv()

def send_telegram_notification(message, status="info"):
    """
    Sends a notification to Telegram via Telegram Bot API.
    Status can be: 'info', 'success', 'warning', 'error'
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not bot_token:
        print("Warning: TELEGRAM_BOT_TOKEN not set. Skipping notification.")
        return
    
    if not chat_id:
        print("Warning: TELEGRAM_CHAT_ID not set. Skipping notification.")
        return
    
    try:
        # Determine emoji based on status
        emoji_map = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌"
        }
        emoji = emoji_map.get(status, "ℹ️")
        
        # Format the message with Markdown
        formatted_message = f"{emoji} *Prefecture Bot - {status.upper()}*\n\n{message}\n\n_Prefecture Slot Checker_"
        
        # Prepare the request to Telegram Bot API
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": formatted_message,
            "parse_mode": "Markdown"
        }
        
        # Send via requests.post
        response = requests.post(url, json=payload)
        
        if response.status_code == 200:
            print(f"✓ Telegram notification sent: {message[:50]}...")
        else:
            print(f"⚠ Telegram notification failed: {response.status_code} {response.text}")
        
    except Exception as e:
        print(f"Error sending Telegram notification: {e}")

def solve_captcha(audio_path):
    """
    Sends the audio file to Gemini API and returns the alphanumeric code.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable not set.")
        return None
    
    genai.configure(api_key=api_key)
    
    try:
        print(f"Uploading {audio_path} to Gemini...")
        myfile = genai.upload_file(audio_path)
        
        model = genai.GenerativeModel("gemini-2.5-pro")
        
        print("Requesting captcha solution...")
        result = model.generate_content(
            [myfile, "\n\n", "Listen to this audio and provide the alphanumeric code you hear. The response should contain ONLY the code, nothing else. The code consists of letters and numbers."]
        )
        return result.text.strip()
    except Exception as e:
        print(f"Error solving captcha: {e}")
        return None

@browser(
    block_images=True,
    headless=True,
    tiny_profile=True,
    profile="bot_profile",
    wait_for_complete_page_load=False,
    add_arguments=[
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-accelerated-2d-canvas",
        "--no-first-run",
        "--no-zygote",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-software-rasterizer",
        "--window-size=1920,1080",
        "--disable-features=VizDisplayCompositor"
    ]
)
def run(driver: Driver, data=None):
    MAX_CAPTCHA_ATTEMPTS = 5  # Max attempts for the entire captcha flow
    MAX_AUDIO_RETRIES = 3     # Max retries for audio capture
    MAX_GEMINI_RETRIES = 3    # Max retries for Gemini decoding
    
    def inject_audio_hooks():
        """Inject JavaScript to intercept audio response body"""
        driver.run_js("""
            window.capturedAudioData = null;
            
            // Override fetch to capture response
            var originalFetch = window.fetch;
            window.fetch = function(input, init) {
                var url = input;
                if (typeof input === 'object' && input.url) {
                    url = input.url;
                }
                
                return originalFetch(input, init).then(response => {
                    if (typeof url === 'string' && url.includes('get=sound')) {
                        // Clone the response so we can read it without affecting the original
                        const clonedResponse = response.clone();
                        clonedResponse.arrayBuffer().then(buffer => {
                            // Convert to base64
                            const bytes = new Uint8Array(buffer);
                            let binary = '';
                            for (let i = 0; i < bytes.byteLength; i++) {
                                binary += String.fromCharCode(bytes[i]);
                            }
                            window.capturedAudioData = btoa(binary);
                        });
                    }
                    return response;
                });
            }

            // Override XHR
            var originalOpen = XMLHttpRequest.prototype.open;
            var originalSend = XMLHttpRequest.prototype.send;
            
            XMLHttpRequest.prototype.open = function(method, url) {
                this._url = url;
                return originalOpen.apply(this, arguments);
            }
            
            XMLHttpRequest.prototype.send = function() {
                if (this._url && this._url.includes('get=sound')) {
                    this.addEventListener('load', function() {
                        if (this.responseType === 'blob' || this.responseType === '') {
                            const reader = new FileReader();
                            reader.onloadend = function() {
                                window.capturedAudioData = reader.result.split(',')[1];
                            }
                            reader.readAsDataURL(new Blob([this.response]));
                        } else if (this.responseType === 'arraybuffer') {
                            const bytes = new Uint8Array(this.response);
                            let binary = '';
                            for (let i = 0; i < bytes.byteLength; i++) {
                                binary += String.fromCharCode(bytes[i]);
                            }
                            window.capturedAudioData = btoa(binary);
                        }
                    });
                }
                return originalSend.apply(this, arguments);
            }
        """)
    
    print("Navigating to prefecture site...")
    driver.get("https://www.rdv-prefecture.interieur.gouv.fr/rdvpref/reservation/demarche/9040/cgu/")
    driver.sleep(2)
    
    # Inject hooks for the first time
    inject_audio_hooks()

    # Main captcha solving loop
    for captcha_attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
        print(f"\n{'='*60}")
        print(f"=== CAPTCHA ATTEMPT {captcha_attempt}/{MAX_CAPTCHA_ATTEMPTS} ===")
        print(f"{'='*60}")
        
        # Reset captured audio data
        driver.run_js("window.capturedAudioData = null;")
        
        saved_audio_path = None
        
        # STEP 1: CAPTURE AUDIO (with retries)
        for audio_retry in range(1, MAX_AUDIO_RETRIES + 1):
            print(f"\n--- Audio Capture Attempt {audio_retry}/{MAX_AUDIO_RETRIES} ---")
            
            # Click "Énoncer le code du captcha"
            print("Clicking 'Énoncer le code du captcha'...")
            try:
                if driver.is_element_present("[title*='noncer le code du captcha']"):
                    driver.click("[title*='noncer le code du captcha']")
                else:
                    driver.click("//*[contains(text(), 'Énoncer le code du captcha')]")
            except Exception as e:
                print(f"Error clicking button: {e}")
                if audio_retry < MAX_AUDIO_RETRIES:
                    print("Retrying audio capture...")
                    driver.sleep(2)
                    continue
                else:
                    break

            print("Waiting for audio to be captured...")
            driver.sleep(6)

            # Retrieve captured audio data
            audio_data_b64 = driver.run_js("return window.capturedAudioData;")

            if audio_data_b64:
                print("✓ Audio data captured!")
                try:
                    audio_bytes = base64.b64decode(audio_data_b64)
                    
                    if len(audio_bytes) < 1024:
                        print("⚠ Audio data too small.")
                        if audio_retry < MAX_AUDIO_RETRIES:
                            print("Retrying audio capture...")
                            driver.run_js("window.capturedAudioData = null;")
                            driver.sleep(2)
                            continue
                    else:
                        print(f"✓ Audio size: {len(audio_bytes)} bytes")
                        
                        # Check duration
                        ext = ".wav"
                        filename = f'output{time.time()}{ext}'
                        with open(filename, "wb") as f:
                            f.write(audio_bytes)
                        print(f"✓ Audio saved: {filename}")
                        
                        try:
                            audio = mutagen.File(filename)
                            if audio and audio.info.length < 1:
                                print("⚠ Audio too short (< 1 second).")
                                if audio_retry < MAX_AUDIO_RETRIES:
                                    print("Retrying audio capture...")
                                    driver.run_js("window.capturedAudioData = null;")
                                    driver.sleep(2)
                                    continue
                            else:
                                saved_audio_path = filename
                                break  # Success! Exit audio retry loop
                        except Exception as e:
                            print(f"⚠ Error checking duration: {e}")
                            saved_audio_path = filename  # Use it anyway
                            break
                            
                except Exception as e:
                    print(f"Error processing audio: {e}")
                    if audio_retry < MAX_AUDIO_RETRIES:
                        print("Retrying audio capture...")
                        driver.sleep(2)
                        continue
            else:
                print("✗ No audio data captured.")
                if audio_retry < MAX_AUDIO_RETRIES:
                    print("Retrying audio capture...")
                    driver.sleep(2)
                    continue
        
        # If we don't have audio after all retries, skip to next captcha attempt
        if not saved_audio_path:
            print("\n✗ Failed to capture audio after all retries.")
            if captcha_attempt < MAX_CAPTCHA_ATTEMPTS:
                print("Moving to next captcha attempt...")
                continue
            else:
                print("Max captcha attempts reached. Giving up.")
                break
        
        # STEP 2: DECODE WITH GEMINI (with retries)
        code = None
        for gemini_retry in range(1, MAX_GEMINI_RETRIES + 1):
            print(f"\n--- Gemini Decode Attempt {gemini_retry}/{MAX_GEMINI_RETRIES} ---")
            code = solve_captcha(saved_audio_path)
            if code:
                print(f"✓ Captcha Code: {code}")
                break
            else:
                print("✗ Failed to get code from Gemini.")
                if gemini_retry < MAX_GEMINI_RETRIES:
                    print("Retrying Gemini decode...")
                    driver.sleep(2)
                    continue
        
        if not code:
            print("\n✗ Failed to decode captcha after all Gemini retries.")
            if captcha_attempt < MAX_CAPTCHA_ATTEMPTS:
                print("Moving to next captcha attempt...")
                continue
            else:
                print("Max captcha attempts reached. Giving up.")
                break
        
        # STEP 3: SUBMIT CAPTCHA
        print(f"\n--- Submitting Captcha Code: {code} ---")
        try:
            driver.type("#captchaFormulaireExtInput", code)
            driver.sleep(0.5)
            
            try:
                driver.click("button[formaction*='_validerCaptcha']")
                print("✓ Clicked Suivant")
            except Exception as click_error:
                driver.run_js("""
                    var btn = document.querySelector("button[formaction*='_validerCaptcha']");
                    if (btn) { btn.click(); }
                """)
                print("✓ Clicked Suivant (via JS)")
            
            # Wait for redirect
            driver.sleep(3)
            
            # Check result
            current_url = driver.current_url
            print(f"\nCurrent URL: {current_url}")
            
            if "error=invalidCaptcha" in current_url:
                print("❌ Invalid captcha!")
                if captcha_attempt < MAX_CAPTCHA_ATTEMPTS:
                    print("Retrying entire captcha flow...")
                    driver.get("https://www.rdv-prefecture.interieur.gouv.fr/rdvpref/reservation/demarche/9040/cgu/")
                    driver.sleep(2)
                    inject_audio_hooks()  # Re-inject hooks!
                    continue
                else:
                    print("Max captcha attempts reached. Giving up.")
                    break
            else:
                print("✅ Captcha validated successfully!")
                
                page_text = driver.run_js("return document.body.innerText;")
                if "Aucun créneau disponible" in page_text:
                    print("📅 'Aucun créneau disponible' - No time slots available")
                    # Don't send notification for this expected case
                    return {"success": True, "slots_available": False, "message": "No time slots available"}
                else:
                    print("🎉 Different content - might have available slots!")
                    # Send notification for potential slots!
                    send_telegram_notification(
                        "⚠️ **Potential Slot Available!**\n\nThe page content is different from usual. There might be available slots!\n\nPlease check: https://www.rdv-prefecture.interieur.gouv.fr/",
                        status="warning"
                    )
                    return {"success": True, "slots_available": True, "message": "Potential slots detected!"}
                
        except Exception as e:
            print(f"✗ Error submitting captcha: {e}")
            error_msg = f"Error during captcha submission: {str(e)}"
            send_telegram_notification(error_msg, status="error")
            
            if captcha_attempt < MAX_CAPTCHA_ATTEMPTS:
                print("Retrying entire captcha flow...")
                driver.get("https://www.rdv-prefecture.interieur.gouv.fr/rdvpref/reservation/demarche/9040/cgu/")
                driver.sleep(2)
                inject_audio_hooks()  # Re-inject hooks!
                continue
            else:
                print("Max captcha attempts reached. Giving up.")
                return {"success": False, "error": error_msg}

    # If we reach here, all captcha attempts failed
    error_msg = "Failed to solve captcha after all attempts"
    print(f"\n❌ {error_msg}")
    send_telegram_notification(error_msg, status="error")
    return {"success": False, "error": error_msg}

if __name__ == "__main__":
    # Load check interval from environment variable
    check_interval = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
    
    print("="*60)
    print("Prefecture Slot Checker - Starting 24/7 Mode")
    print(f"Check interval: {check_interval} seconds")
    print("="*60)
    
    send_telegram_notification(
        f"🤖 **Bot Started**\n\nPrefecture slot checker is now running.\nCheck interval: {check_interval} seconds",
        status="info"
    )
    
    iteration = 0
    while True:
        iteration += 1
        print(f"\n{'='*60}")
        print(f"=== ITERATION {iteration} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        print(f"{'='*60}\n")
        
        try:
            result = run()
            print(f"\nResult: {result}")
            
        except Exception as e:
            error_msg = f"Critical error in main loop: {str(e)}"
            print(f"\n❌ {error_msg}")
            send_telegram_notification(error_msg, status="error")
        
        print(f"\n{'='*60}")
        print(f"Waiting {check_interval} seconds before next check...")
        print(f"{'='*60}\n")
        time.sleep(check_interval)
