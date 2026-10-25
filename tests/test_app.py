
import os
import unittest
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait


class InventoryAppTest(unittest.TestCase):

    def setUp(self):
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        self.driver = webdriver.Chrome(options=options)
        self.base_url = os.environ.get(
            "APP_URL", "http://127.0.0.1:5056"
        )

    def test_homepage_and_login_page(self):
        driver = self.driver
        driver.get(self.base_url)

        # Check the welcome page
        self.assertIn("Your inventory. Under control.", driver.page_source)

        # Open and check the login page
        driver.get(self.base_url + "/login")
        WebDriverWait(driver, 10).until(
            lambda d: d.find_element(By.NAME, "username")
        )
        self.assertTrue(
            driver.find_element(By.NAME, "password").is_displayed()
        )

    def tearDown(self):
        if hasattr(self, "driver"):
            self.driver.quit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
