"""Plots CloudWatch metrics from a load test: CPU, instance count and request rate.

Usage:
  python scripts/plot_results.py --minutes 40
  python scripts/plot_results.py --start 2026-11-24T04:09 --end 2026-11-24T04:29   (UTC)
Writes docs/results/scaling.png (300 dpi, sized for a report column).
"""
import argparse
import datetime as dt
import os

import boto3
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

ASG_NAME = "cloud-ha-web-asg"
ALB_NAME = "cloud-ha-alb"
INK, GREY, ACCENT = "#222222", "#9a9a9a", "#1f4e9c"


def series(cw, namespace, metric, dims, stat, start, end):
    resp = cw.get_metric_data(
        MetricDataQueries=[{
            "Id": "m",
            "MetricStat": {
                "Metric": {"Namespace": namespace, "MetricName": metric,
                           "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()]},
                "Period": 60,
                "Stat": stat,
            },
        }],
        StartTime=start, EndTime=end, ScanBy="TimestampAscending",
    )
    data = resp["MetricDataResults"][0]
    return data["Timestamps"], data["Values"]


def utc(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=40)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--lb-dimension", help="app/cloud-ha-alb/<id>; needed once the load balancer is deleted")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "docs", "results", "scaling.png"))
    args = parser.parse_args()

    end = utc(args.end) if args.end else dt.datetime.now(dt.timezone.utc)
    start = utc(args.start) if args.start else end - dt.timedelta(minutes=args.minutes)
    cw = boto3.client("cloudwatch", region_name=args.region)
    lb_dim = args.lb_dimension
    if not lb_dim:
        lb_arn = boto3.client("elbv2", region_name=args.region).describe_load_balancers(
            Names=[ALB_NAME])["LoadBalancers"][0]["LoadBalancerArn"]
        lb_dim = lb_arn.split(":loadbalancer/")[1]

    cpu = series(cw, "AWS/EC2", "CPUUtilization", {"AutoScalingGroupName": ASG_NAME}, "Average", start, end)
    in_service = series(cw, "AWS/AutoScaling", "GroupInServiceInstances", {"AutoScalingGroupName": ASG_NAME},
                        "Maximum", start, end)
    requests = series(cw, "AWS/ApplicationELB", "RequestCount", {"LoadBalancer": lb_dim}, "Sum", start, end)

    plt.rcParams.update({"font.family": "serif", "font.serif": ["Cambria", "Times New Roman", "DejaVu Serif"],
                         "font.size": 8.5, "axes.edgecolor": INK, "axes.linewidth": 0.6,
                         "xtick.color": INK, "ytick.color": INK, "axes.labelcolor": INK})
    fig, axes = plt.subplots(3, 1, figsize=(6.3, 4.6), sharex=True,
                             gridspec_kw={"height_ratios": [1.2, 0.8, 1]})
    axes[0].plot(*cpu, color=INK, linewidth=1.2)
    axes[0].axhline(50, color=GREY, linestyle=(0, (4, 3)), linewidth=0.8)
    axes[0].text(0.99, 0.54, "scaling target 50%", color=GREY, fontsize=7.5, ha="right",
                 transform=axes[0].transAxes)
    axes[0].set_ylabel("CPU (%)")
    axes[0].set_ylim(0, 100)
    axes[1].step(*in_service, where="post", color=ACCENT, linewidth=1.4)
    axes[1].set_ylabel("Instances")
    axes[1].set_ylim(0, 5)
    axes[1].set_yticks([0, 2, 4])
    axes[2].bar(requests[0], requests[1], width=0.0005, color=GREY)
    axes[2].set_ylabel("Requests/min")
    for ax in axes:
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(width=0.6, length=3)
        ax.yaxis.grid(True, color="#e6e6e6", linewidth=0.5)
        ax.set_axisbelow(True)
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=dt.timezone.utc))
    axes[2].set_xlabel("Time (UTC)")
    fig.align_ylabels(axes)
    fig.tight_layout(h_pad=0.6)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fig.savefig(args.out, dpi=300)
    print("Saved", os.path.abspath(args.out))


if __name__ == "__main__":
    main()
