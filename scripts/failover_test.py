"""Failover test: terminate one web instance and measure the impact and recovery.

Probes the site once per second while it terminates a random in-service instance, then
records when the Auto Scaling group launches a replacement and when the load balancer
marks it healthy. Uses the same AWS credentials as the AWS CLI.

Usage: python scripts/failover_test.py http://<alb-dns>
"""
import argparse
import random
import threading
import time
import urllib.error
import urllib.request

import boto3

ASG_NAME = "cloud-ha-web-asg"
TG_NAME = "cloud-ha-tg"


def probe(url, stop, log):
    while not stop.is_set():
        started = time.time()
        try:
            with urllib.request.urlopen(url + "/", timeout=10) as resp:
                ok = resp.status == 200
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            ok = False
        log.append((started, ok))
        time.sleep(max(0.0, 1 - (time.time() - started)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    autoscaling = boto3.client("autoscaling", region_name=args.region)
    ec2 = boto3.client("ec2", region_name=args.region)
    elbv2 = boto3.client("elbv2", region_name=args.region)
    tg_arn = elbv2.describe_target_groups(Names=[TG_NAME])["TargetGroups"][0]["TargetGroupArn"]

    def asg_instances():
        group = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])["AutoScalingGroups"][0]
        return {i["InstanceId"]: i["LifecycleState"] for i in group["Instances"]}

    def healthy_targets():
        desc = elbv2.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
        return {d["Target"]["Id"] for d in desc if d["TargetHealth"]["State"] == "healthy"}

    before = asg_instances()
    victim = random.choice([i for i, s in before.items() if s == "InService"])
    log, stop = [], threading.Event()
    prober = threading.Thread(target=probe, args=(url, stop, log), daemon=True)
    prober.start()
    time.sleep(20)  # baseline

    t0 = time.time()
    ec2.terminate_instances(InstanceIds=[victim])
    print(f"{time.strftime('%H:%M:%S')} terminated {victim}")

    launched_at = healthy_at = replacement = None
    while time.time() - t0 < 1200:
        current = asg_instances()
        new = [i for i in current if i not in before]
        if new and launched_at is None:
            replacement, launched_at = new[0], time.time()
            print(f"{time.strftime('%H:%M:%S')} replacement {replacement} launched after {launched_at - t0:.0f} s")
        if replacement and replacement in healthy_targets():
            healthy_at = time.time()
            print(f"{time.strftime('%H:%M:%S')} replacement healthy after {healthy_at - t0:.0f} s")
            break
        time.sleep(5)

    time.sleep(10)
    stop.set()
    prober.join()

    during = [ok for started, ok in log if started >= t0]
    failed = [started for started, ok in log if not ok]
    print("\n--- Failover result ---")
    print(f"Probe requests after termination: {len(during)}, failed: {len(during) - sum(during)}")
    if failed:
        print(f"Failures between +{min(failed) - t0:.0f} s and +{max(failed) - t0:.0f} s")
    if launched_at:
        print(f"Replacement launched: {launched_at - t0:.0f} s after termination")
    if healthy_at:
        print(f"Back to full capacity: {healthy_at - t0:.0f} s ({(healthy_at - t0) / 60:.1f} min)")


if __name__ == "__main__":
    main()
