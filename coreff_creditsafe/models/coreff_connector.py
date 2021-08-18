# ©2018-2019 Article714
# # License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import json
from requests import Session
from odoo.tools.config import config
from odoo import api, models
from odoo.exceptions import UserError,Warning
import urllib.parse

class CustomSessionProxy(Session):
    def __init__(self):
        super().__init__()

        proxy_http = config.get("proxy_http")
        proxy_https = config.get("proxy_https")

        self.proxies = {
            "http": proxy_http,
            "https": proxy_https,
        }


class CoreffConnector(models.Model):
    _inherit = "coreff.connector"

    @api.model
    def creditsafe_authenticate(self, url, username, password):
        """
        Auto authent to access CreditSafe
        """
        headers = {
            "accept": "application/json",
            "Content-type": "application/json",
        }

        data = {"username": username, "password": password}

        with CustomSessionProxy() as session:
            response = session.post(
                "{}/authenticate".format(url),
                data=json.dumps(data),
                headers=headers,
            )

            if response.status_code == 200:
                content = response.json()

                self.env["coreff.credentials"].update_token(
                    url, username, content["token"]
                )
                return content["token"]
            if response.status_code == 401:
                return False
            else:
                return self.format_error(response)

    @api.model
    def creditsafe_get_companies(self, arguments, retry=False):
        """
        Get companies
        """
        settings = self.get_company_creditsafe_settings(arguments["user_id"])
        url = settings["url"]
        token = settings["token"]

        if url:
            headers = {
                "accept": "application/json",
                "Content-type": "application/json",
                "Authorization": token if token else "",
            }

            #CM: Lower pagesize from 200 to 10
            call_url = "{}/companies?language=EN&page=1&pageSize=10".format(
                url
            )

            #CM: Using urllib.parse.quote to escape values
            #to use in URL parameters (accounting for spaces)
            if arguments["valueIsCompanyCode"]:
                query = urllib.parse.quote(arguments["value"])
                call_url += "&regNo={}".format(query)
            else:
                query = urllib.parse.quote(arguments["value"])
                call_url += "&name={}".format(query)

            code = False
            if arguments["country_id"]:
                code = (
                    self.env["res.country"]
                    .search([("id", "=", arguments["country_id"])])[0]
                    .code
                )
                call_url += "&countries={}".format(code)
            else:
                #CM: If no country specified, search in ALL (much slower)
                call_url += "&countries=US,GB,SE,NO,NL,MX,LU,JP,IT,IE,DE,FR,DK,CA,BE"

            #CM: Add constraint for all other countries that support the HeadOffice category including FR
            if arguments.get("is_head_office", True) and code in ["FR","CA","DK","US","IT","JP","LU","NL","NO"]:
                call_url += "&officeType=HeadOffice"

            with CustomSessionProxy() as session:
                response = session.get(call_url, headers=headers)

                if response.status_code == 200:
                    content = response.json()

                    content["companies"].sort(
                        key=lambda x: (
                            x.get("name"),
                            x.get("address", {}).get("city", ""),
                        )
                    )

                    suggestions = []
                    for company in content.get("companies", {}):
                        suggestion = {}
                        suggestion["creditsafe_company_id"] = company.get(
                            "id", ""
                        )
                        suggestion["name"] = company.get("name", "")
                        suggestion["coreff_company_code"] = company.get(
                            "regNo", ""
                        )
                        suggestion["street"] = company.get("address", {}).get(
                            "street", ""
                        )
                        suggestion["city"] = company.get("address", {}).get(
                            "city", ""
                        )
                        suggestion["zip"] = company.get("address", {}).get(
                            "postCode", ""
                        )
                        suggestion["country_id"] = company.get("country", "")
                        #CM: VAT and phone numbers only stored if present
                        #otherwise crashes.
                        optional_val = company.get("vatNo", [""])
                        if len(optional_val)>0: suggestion["vat"] = optional_val[0]
                        optional_val = company.get("phoneNumbers", [""])
                        if len(optional_val)>0: suggestion["phone"] = optional_val[0]
                        suggestions.append(suggestion)
                    return suggestions
                elif response.status_code in (401, 403):
                    if not retry:
                        res = self.creditsafe_authenticate(
                            settings["url"],
                            settings["username"],
                            settings["password"],
                        )
                        if res:
                            return self.creditsafe_get_companies(
                                arguments, True
                            )
                        else:
                            return self.format_error(response)
                    else:
                        return self.format_error(response)
                else:
                    return self.format_error(response)

    @api.model
    def creditsafe_get_company(self, arguments, retry=False):
        """
        Get company information
        """
        settings = self.get_company_creditsafe_settings(arguments["user_id"])
        url = settings["url"]
        token = settings["token"]

        if url:
            headers = {
                "accept": "application/json",
                "Content-type": "application/json",
                "Authorization": token if token else "",
            }

            call_url = "{}/companies/{}".format(url, arguments["company_id"])

            with CustomSessionProxy() as session:
                response = session.get(call_url, headers=headers)

                if response.status_code == 200:
                    content = response.json()
                    return content
                elif response.status_code in (401, 403):
                    if not retry:
                        res = self.creditsafe_authenticate(
                            settings["url"],
                            settings["username"],
                            settings["password"],
                        )
                        if res:
                            return self.creditsafe_get_company(arguments, True)
                        else:
                            return self.format_error(response)
                    else:
                        return self.format_error(response)
                else:
                    return self.format_error(response)

    def get_company_creditsafe_settings(self, user_id):
        """
        Get company settings for CreditSafe
        """
        res = {}
        user = self.env["res.users"].browse(user_id)
        company = user.company_id
        res["url"] = company.get_parent_creditsafe_field("creditsafe_url")
        res["username"] = company.get_parent_creditsafe_field(
            "creditsafe_username"
        )
        res["password"] = company.get_parent_creditsafe_field(
            "creditsafe_password"
        )
        res["token"] = (
            self.env["coreff.credentials"]
            .get_credentials(res["url"], res["username"])
            .token
        )
        return res

    def format_error(self, response):
        """
        Format api response
        """
        res = {}
        res["title"] = "[{}] : {}".format(
            response.status_code, response.reason
        )
        res["body"] = response.content
        return {"error": res}
