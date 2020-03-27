#   Copyright 2019 Illumio, Inc.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.


from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.sql import SqlManagementClient
from azure.common.credentials import ServicePrincipalCredentials
import requests
import os
import time
import urllib
import ipaddress


# PCE API request call using requests module
def pce_request(pce, org_id, key, secret, verb, path, params=None,
                data=None, json=None, extra_headers=None):
    base_url = os.path.join(pce, 'orgs', org_id)
    headers = {
              'user-agent': 'azure-db-agent',
            }
    if extra_headers:
        headers.update(extra_headers)
    response = requests.request(verb,
                                os.path.join(base_url, path),
                                auth=(key, secret),
                                headers=headers,
                                params=params,
                                json=json,
                                data=data)
    return response


# Fetching the Illumio security policy for Azure MS SQL
def update_illumio_policies():
    status = {}
    status['ip_list'] = []
    status['db_dict'] = {}
    # Getting the data from environment variables for the PCE API request
    pce_api = int(os.environ['ILO_API_VERSION'])
    pce = os.path.join('https://' + os.environ['ILLUMIO_SERVER'] + ':' + os.environ['ILO_PORT'], 'api', 'v%d' % pce_api)
    org_id = os.environ['ILO_ORG_ID']
    key = 'api_' + os.environ['ILO_API_KEY_ID']
    secret = os.environ['ILO_API_KEY_SECRET']
    sec_policy = 'sec_policy/draft/rule_sets/' + str(os.environ['ILLUMIO_RULESET_KEY']) + '?representation=rule_set_services_labels_and_names'
    response = pce_request(pce, org_id, key, secret, 'GET', sec_policy).json()
    # print('Received the following response from Illumio PCE for sec_policy')
    # print(response)
    status = {}
    status['ip_list'] = {}
    status['db_instance_identifier'] = {}
    for rule in response['rules']:
        if rule['unscoped_consumers'] == True and 'ip_list' not in rule['consumers'][0]:
            sql_rule = rule
            role_label = sql_rule['consumers'][0]['label']['href']
            role_label = str('[["') + role_label + str('"]]')
            query = urllib.parse.quote_plus(role_label)
            workload = 'workloads?representation=workload_labels&labels=' + query
            wl_response = pce_request(pce, org_id, key, secret, 'GET', workload).json()
            # print('This is the response for consumer workloads from the PCE ', wl_response)
            pce_ip_list = []
            for workload in wl_response:
                pce_ip_list.append(workload['interfaces'][0]['address'])
                if len(workload.get('interfaces')) >= 2:
                    pce_ip_list.append(workload['interfaces'][1]['address'])
            status['ip_list'][response['rules'].index(rule)] = pce_ip_list
            # print('This is the ip list for workloads from PCE ', status)
            provider_role_label = sql_rule['providers'][0]['label']['href']
            provider_role_label = str('[["') + provider_role_label + str('"]]')
            provider_query = urllib.parse.quote_plus(provider_role_label)
            provider = 'workloads?representation=workload_labels&labels=' + provider_query
            provider_response = pce_request(pce, org_id, key, secret, 'GET', provider).json()
            # print('Received the following response from Illumio PCE for provider workloads')
            # print(provider_response)
            if provider_response is not None:
                sql_fqdn = provider_response[0]['hostname']
                # sql_workload = provider_response[0]['href']
                # print('The MS SQL FQDN is ', sql_fqdn, ' and the bound workload is ', sql_workload)
                db_instance_identifier = sql_fqdn.split('.')[0]
                status['db_instance_identifier'][response['rules'].index(rule)] = db_instance_identifier
            else:
                print('No provider database found')

        elif rule['unscoped_consumers'] == True and 'ip_list' in rule['consumers'][0]:
            sql_rule = rule
            ip_list = []
            list_href = sql_rule['consumers'][0]['ip_list']['href']
            ip_list_key = list_href.split('/')[-1]
            ip_list_href = 'sec_policy/draft/ip_lists/' + str(ip_list_key)
            ip_list_response = pce_request(pce, org_id, key, secret, 'GET', ip_list_href).json()
            for ip_obj in ip_list_response['ip_ranges']:
                pce_ip_list = {}
                if ip_obj.get('from_ip') is not None:
                    pce_ip_list['start'] = ip_obj.get('from_ip')
                if ip_obj.get('to_ip') is not None:
                    pce_ip_list['end'] = ip_obj.get('to_ip')
                else:
                    pce_ip_list['end'] = ip_obj.get('from_ip')
                ip_list.append(pce_ip_list)
            status['ip_list'][response['rules'].index(rule)] = ip_list
            provider_role_label = sql_rule['providers'][0]['label']['href']
            provider_role_label = str('[["') + provider_role_label + str('"]]')
            provider_query = urllib.parse.quote_plus(provider_role_label)
            provider = 'workloads?representation=workload_labels&labels=' + provider_query
            provider_response = pce_request(pce, org_id, key, secret, 'GET', provider).json()
            # print('Received the following response from Illumio PCE for provider workloads')
            # print(provider_response)
            if provider_response is not None:
                sql_fqdn = provider_response[0]['hostname']
                # sql_workload = provider_response[0]['href']
                # print('The MS SQL FQDN is ', sql_fqdn, ' and the bound workload is ', sql_workload)
                db_instance_identifier = sql_fqdn.split('.')[0]
                status['db_instance_identifier'][response['rules'].index(rule)] = db_instance_identifier

            else:
                print('No provider database found')
        else:
            return None
    return status


def create_azure_fw_rule(status_dict):
    RESOURCE_GROUP = os.environ['RESOURCE_GROUP']
    subscription_id = os.environ.get(
        'AZURE_SUBSCRIPTION_ID',
        '11111111-1111-1111-1111-111111111111')
    credentials = ServicePrincipalCredentials(
        client_id=os.environ['AZURE_CLIENT_ID'],
        secret=os.environ['AZURE_CLIENT_SECRET'],
        tenant=os.environ['AZURE_TENANT_ID']
                                             )
    resource_client = ResourceManagementClient(credentials, subscription_id)
    sql_client = SqlManagementClient(credentials, subscription_id)
    resource_client.providers.register('Microsoft.Sql')
    for index in range(len(status_dict['ip_list'])):
        for entry in status_dict['ip_list'][index]:
            if type(entry) == dict:
                SQL_SERVER = status_dict['db_instance_identifier'][index]
                if '/' in entry['start']:
                    n = ipaddress.IPv4Network(entry['start'])
                    entry['start'], entry['end'] = n[0], n[-1]
                rule_name = 'firewall_rule_for_ip_list_' + str(entry['start'])
                firewall_rule = sql_client.firewall_rules.create_or_update(RESOURCE_GROUP, SQL_SERVER, rule_name, entry['start'], entry['end'])
                print('Adding the rule', firewall_rule)
            else:
                SQL_SERVER = status_dict['db_instance_identifier'][index]
                rule_name = 'firewall_rule_for_workload_' + str(entry)
                firewall_rule = sql_client.firewall_rules.create_or_update(RESOURCE_GROUP, SQL_SERVER, rule_name, entry, entry)
                print('Adding the rule', firewall_rule)
    return firewall_rule


def main():
    while(True):
        status_dict = update_illumio_policies()
        # Open access to this server for IPs
        create_azure_fw_rule(status_dict)
        time.sleep(int(os.environ['POLL_TIMER']))


if __name__ == '__main__':
    main()
