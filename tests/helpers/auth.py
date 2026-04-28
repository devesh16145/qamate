from playwright.sync_api import Page

def login(page: Page, base_url: str, email: str, password: str):
    """Centralized login helper."""
    page.goto(base_url + "login")
    page.wait_for_load_state("networkidle")

    email_field = page.locator('input[placeholder*="Email"], input[type="email"]').first
    password_field = page.locator('input[type="password"]').first
    sign_in_button = page.locator('button:has-text("Sign In"), button[type="submit"]').first

    email_field.click()
    page.keyboard.type(email, delay=50)
    
    password_field.click()
    page.keyboard.type(password, delay=50)
    
    sign_in_button.click()
    page.wait_for_load_state("networkidle")
