"""Check ECR Scan results for all service container images."""

import argparse
import json
import os
import inspect
import requests
import boto3


class ECRScanChecker:
    """Check ECR Scan results for all service container images."""
    aws_account_id = ''
    iam_role_name = ''
    aws_iam_session = ''
    aws_ecr_client = ''
    images_to_check = []
    tag = ''
    report = ''
    report_limit = ''

    def __init__(self, iam_role_name, ecr_aws_account_id, report_limit, search_term):
        self.iam_role_name = iam_role_name
        self.report_limit = int(report_limit)
        self.aws_account_id = ecr_aws_account_id
        self.set_iam_role_session()
        self.aws_ecr_client = boto3.client(
            'ecr',
            region_name='eu-west-1',
            aws_access_key_id=self.aws_iam_session['Credentials']['AccessKeyId'],
            aws_secret_access_key=self.aws_iam_session['Credentials']['SecretAccessKey'],
            aws_session_token=self.aws_iam_session['Credentials']['SessionToken'])
        self.images_to_check = self.get_repositories(search_term)

    def set_iam_role_session(self):
        """Create an IAM role session."""
        role_arn = 'arn:aws:iam::{}:role/{}'.format(
            self.aws_account_id, self.iam_role_name)

        sts = boto3.client(
            'sts',
            region_name='eu-west-1',
        )
        session = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName='checking_ecr_image_scan',
            DurationSeconds=900
        )
        self.aws_iam_session = session

    def get_repositories(self, search_term):
        """Get all ECR repositories using search."""
        images_to_check = []
        response = self.aws_ecr_client.describe_repositories()
        for repository in response["repositories"]:
            if search_term in repository["repositoryName"]:
                images_to_check.append(repository["repositoryName"])
        return images_to_check

    def recursive_wait(self, tag):
        """Iteratively wait for all all available image scans to complete."""
        print("Waiting for ECR scans to complete...")
        for image in self.images_to_check:
            self.wait_for_scan_completion(image, tag)
        print("ECR image scans complete")

    def wait_for_scan_completion(self, image, tag):
        """Wait until ECR scans have completed for image:tag."""
        try:
            waiter = self.aws_ecr_client.get_waiter('image_scan_complete')
            waiter.wait(
                repositoryName=image,
                imageId={
                    'imageTag': tag
                },
                WaiterConfig={
                    'Delay': 5,
                    'MaxAttempts': 60
                }
            )
        except:
            print("No ECR image scan results for image {0}, tag {1}".format(
                image, tag))

    def recursive_check_make_report(self, tag):
        """Construct report text from ECR scan findings."""
        print("Checking ECR scan results...")
        for image in self.images_to_check:
            try:
                findings = self.get_ecr_scan_findings(image, tag)[
                    "imageScanFindings"]
                if findings["findings"] != []:

                    counts = findings["findingSeverityCounts"]
                    title = "\n\n:warning: *AWS ECR Scan found results for {}:* \n".format(
                        image)
                    severity_counts = inspect.cleandoc("""Severity finding counts: {}
                                      Displaying the first {} in order of severity

                                      """.format(counts, self.report_limit))
                    self.report = title + severity_counts

                    for finding in findings["findings"]:
                        cve = finding["name"]

                        description = "None"
                        if "description" in finding:
                            description = finding["description"]

                        severity = finding["severity"]
                        link = finding["uri"]
                        result = inspect.cleandoc("""*Image:* {0}
                                  **Tag:* {1}
                                  *Severity:* {2}
                                  *CVE:* {3}
                                  *Description:* {4}
                                  *Link:* {5}

                                  """.format(image, tag, severity, cve, description, link))
                        self.report += result
                    print(self.report)
            except:
                print("Unable to get ECR image scan results for image {0}, tag {1}".format(
                    image, tag))

    def get_ecr_scan_findings(self, image, tag):
        """Get ECR scan findings for image:tag."""
        response = self.aws_ecr_client.describe_image_scan_findings(
            repositoryName=image,
            imageId={
                'imageTag': tag
            },
            maxResults=self.report_limit
        )
        return response

    def finalise_report(self):
        """Post to Slack using slack webhook."""
        if self.report != "":
            branch_info = "\n*Github Branch:* {0}\n*CircleCI Job Link:* {1}\n\n".format(
                os.getenv('CIRCLE_BRANCH', ""),
                os.getenv('CIRCLE_BUILD_URL', ""))
            self.report += branch_info
            print(self.report)

    def post_to_slack(self, slack_webhook):
        """Post to Slack using slack webhook."""
        post_data = json.dumps({"text": self.report})
        response = requests.post(
            slack_webhook, data=post_data,
            headers={'Content-Type': 'application/json'}
        )
        if response.status_code != 200:
            raise ValueError(
                'Request to slack returned an error %s, the response is:\n%s'
                % (response.status_code, response.text)
            )


def main():
    """Check ECR Scan results for all service container images."""
    parser = argparse.ArgumentParser(
        description="Check ECR Scan results for all service container images.")
    parser.add_argument("--iam_role_name",
                        help="Name of the iam role to use when creating AWS STS sessions")
    parser.add_argument("--ecr_aws_account_id",
                        help="AWS Account ID where ECR lives")
    parser.add_argument("--search",
                        default="",
                        help="The root part of the ECR repositry path, for example online-lpa")
    parser.add_argument("--tag",
                        default="latest",
                        help="Image tag to check scan results for.")
    parser.add_argument("--result_limit",
                        default=5,
                        help="How many results for each image to return. Defaults to 5")
    parser.add_argument("--slack_webhook",
                        default=os.getenv('SLACK_WEBHOOK'),
                        help="Webhook to use, determines what channel to post to")
    parser.add_argument("--post_to_slack",
                        default=True,
                        help="Optionally turn off posting messages to slack")

    args = parser.parse_args()
    work = ECRScanChecker(
        args.iam_role_name, args.ecr_aws_account_id, args.result_limit, args.search)
    work.recursive_wait(args.tag)
    work.recursive_check_make_report(args.tag)
    work.finalise_report()
    if args.post_to_slack and args.slack_webhook is not None:
        work.post_to_slack(args.slack_webhook)
    else:
        print("No slack webhook provided, skipping post of results to slack")


if __name__ == "__main__":
    main()
