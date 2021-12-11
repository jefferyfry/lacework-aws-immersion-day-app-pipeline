#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
import json

import boto3
import logging
import os

import urllib3
from crhelper import CfnResource

SUCCESS = "SUCCESS"
FAILED = "FAILED"

http = urllib3.PoolManager()

LOGLEVEL = os.environ.get('LOGLEVEL', logging.INFO)
logger = logging.getLogger()
logger.setLevel(LOGLEVEL)
logging.getLogger("boto3").setLevel(logging.CRITICAL)
logging.getLogger("botocore").setLevel(logging.CRITICAL)
session = boto3.Session()

helper = CfnResource(json_logging=False, log_level="INFO", boto_level="CRITICAL", sleep_on_delete=15)


def lambda_handler(event, context):
  logger.info("resources.lambda_handler called.")
  try:
    if "RequestType" in event: helper(event, context)
  except Exception as e:
    helper.init_failure(e)


@helper.create
@helper.update
def create(event, context):
  logger.info("resources.create called and does nothing right now.")
  send_cfn_response(event, context, SUCCESS, {})
  # add honeycomb telemetry


@helper.delete
def delete(event, context):
  logger.info("resources.delete called.")

  try:
    logger.info("Deleting artifact s3 bucket")
    bucket = os.environ['artifact_bucket']
    s3 = boto3.resource('s3')
    bucket = s3.Bucket(bucket)
    bucket.objects.delete()
    logger.info(bucket.delete())
  except Exception as delete_bucket_exception:
    logger.warning("Problem occurred while deleting s3 bucket: {}".format(delete_bucket_exception))

  try:
    logger.info("Deleting respository")
    ecr_client = session.client("ecr")
    aws_account_id = context.invoked_function_arn.split(":")[4]
    logger.info(ecr_client.delete_repository(
      registryId=aws_account_id,
      repositoryName="demo-app",
      force=True
    ))
  except Exception as delete_repo_exception:
    logger.warning("Problem occurred while deleting repository: {}".format(delete_repo_exception))

  try:
    eks_client = session.client("eks")
    aws_account_id = context.invoked_function_arn.split(":")[4]

    cluster = "{}-eks".format(aws_account_id)
    waiter = eks_client.get_waiter('nodegroup_deleted')
    ngdict = eks_client.list_nodegroups(
      clusterName=cluster
    )
    for ng in ngdict['nodegroups']:
      logger.info(eks_client.delete_nodegroup(
        clusterName=cluster,
        nodegroupName=ng
      ))

      waiter.wait(
        clusterName=cluster,
        nodegroupName=ng,
        WaiterConfig={
          'Delay': 30,
          'MaxAttempts': 20
        }
      )
  except Exception as delete_ng_exception:
    logger.warning("Problem occurred while deleting cluster nodegroup: {}".format(delete_ng_exception))

  try:
    response = eks_client.delete_cluster(
      name=cluster
    )
    logger.info("Deleted cluster {} {}".format(cluster, response))
  except Exception as delete_cluster_exception:
    logger.warning("Problem occurred while deleting cluster: {}".format(delete_cluster_exception))

  send_cfn_response(event, context, SUCCESS, {})


def send_cfn_response(event, context, response_status, response_data, physical_resource_id=None, no_echo=False,
                      reason=None):
  response_url = event['ResponseURL']

  logger.info(response_url)

  response_body = {
    'Status': response_status,
    'Reason': reason or "See the details in CloudWatch Log Stream: {}".format(context.log_stream_name),
    'PhysicalResourceId': physical_resource_id or context.log_stream_name,
    'StackId': event['StackId'],
    'RequestId': event['RequestId'],
    'LogicalResourceId': event['LogicalResourceId'],
    'NoEcho': no_echo,
    'Data': response_data
  }

  json_response_body = json.dumps(response_body)

  logger.info("Response body: {}".format(json_response_body))

  headers = {
    'content-type': '',
    'content-length': str(len(json_response_body))
  }

  try:
    response = http.request('PUT', response_url, headers=headers, body=json_response_body)
    logger.info("Status code: {}".format(response.status))

  except Exception as e:
    logger.error("send_cfn_response error {}".format(e))
