# Code walkthrough

A file-by-file explanation of everything in this repo, written so you can explain any line if asked.
Read it with the code open next to it.

## Part 1: How to read Terraform (5 minutes)

Every `.tf` file is made of blocks. There are only six kinds used here:

| Block | Meaning | Example |
|---|---|---|
| `resource "TYPE" "NAME" { }` | **Create** something in AWS | `resource "aws_lb" "web"` creates a load balancer |
| `data "TYPE" "NAME" { }` | **Look up** something that already exists (creates nothing) | `data "aws_vpc" "default"` finds the default VPC |
| `variable "NAME" { }` | A setting with a default value you can override | `var.asg_max` = 4 |
| `locals { }` | A named constant used inside the code | `local.app_port` = 8000 |
| `output "NAME" { }` | A value printed after `apply` | `app_url` |
| `terraform { }` / `provider { }` | Which plugin to use and how to connect to AWS | `versions.tf` |

**References.** A resource is referred to as `TYPE.NAME.ATTRIBUTE`. For example, `aws_security_group.web.id`
means "the ID of the security group I named `web`". The ID doesn't exist until AWS creates it, so
Terraform fills it in at apply time.

**Dependency order comes from references.** Because the RDS block mentions `aws_security_group.db.id`,
Terraform knows to create the security group first. Nobody writes the order by hand. Terraform builds a graph
from the references and creates independent things in parallel.

**`${...}` is string interpolation.** `"${var.project}-alb"` becomes `"cloud-ha-alb"`.

**File names don't matter to Terraform.** It reads every `.tf` file in the folder as one big file. The split
into `network.tf`, `database.tf` and so on is only to keep things organised.

## Part 2: The files, in the order to read them

### `versions.tf`: setup

```hcl
required_providers { aws = { source = "hashicorp/aws", version = "~> 6.0" } }
```
The AWS provider is the plugin that turns these blocks into AWS API calls. `~> 6.0` means any 6.x version.
`.terraform.lock.hcl` pins the exact version (6.73.0), like `package-lock.json`.

```hcl
default_tags { tags = { Project = var.project, ManagedBy = "terraform" } }
```
Adds these two tags to **every** resource automatically. That's why you can open any resource in the
console and prove Terraform made it.

### `variables.tf`: the knobs

All the numbers you might want to change live here: region, AZs, instance type, ASG min/max/desired (2/4/2),
CPU target (50%), DB size, alert email.

```hcl
validation { condition = length(var.azs) >= 2 ... }
```
Refuses to run with fewer than two AZs, because a single AZ wouldn't be high availability.

### `network.tf`: VPC, subnets, security groups

```hcl
data "aws_vpc" "default" { default = true }
```
Looks up the default VPC that every AWS account has. It's a `data` block, so nothing is created.

```hcl
data "aws_subnet" "selected" {
  for_each          = toset(var.azs)
  availability_zone = each.value
  default_for_az    = true
}
```
`for_each` repeats the block once per AZ, so this finds the default subnet in `us-east-1a` and in `us-east-1b`.

```hcl
locals { subnet_ids = [for az in var.azs : data.aws_subnet.selected[az].id] }
```
A list comprehension (like Python's `[x.id for x in ...]`) that turns those two subnets into a plain list of IDs.
The ALB, ASG and RDS all use this list.

**Three security groups, five rules.** The groups themselves are empty; each rule is its own resource:

| Rule | Group | Direction | From / to | Port |
|---|---|---|---|---|
| `alb_http` | alb | in | `0.0.0.0/0` (internet) | 80 |
| `alb_to_web` | alb | out | web SG | 8000 |
| `web_from_alb` | web | in | alb SG | 8000 |
| `web_all_out` | web | out | anywhere (`ip_protocol = "-1"` = all) | all |
| `db_from_web` | db | in | web SG | 3306 |

The key line is `referenced_security_group_id`. Instead of an IP range, the rule says "anything that is a member of
that other security group". So a brand-new instance from the ASG is allowed into RDS automatically, because it
launches inside the web SG.

The db SG has **no** outbound rule, so the database can't start connections to anything.

### `iam.tf`: permissions for the servers

```hcl
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions    = ["sts:AssumeRole"]
    principals { type = "Service", identifiers = ["ec2.amazonaws.com"] }
  }
}
```
The **trust policy** controls who may wear this role. Here, only the EC2 service can.
`aws_iam_policy_document` is a helper that writes the IAM JSON for you.

The two attachments are AWS-managed policies:
- `AmazonSSMManagedInstanceCore` enables Session Manager (shell access without SSH).
- `CloudWatchAgentServerPolicy` allows sending OS metrics to CloudWatch.

`web_app` is the custom **permissions policy**, with three statements:
- `s3:GetObject/PutObject/DeleteObject` on `bucket-arn/*` (the objects)
- `s3:ListBucket` on `bucket-arn` (the bucket itself). This needs a separate statement because the
  resource format is different.
- `secretsmanager:GetSecretValue` on exactly one secret, the DB password.

That's least privilege: the servers can't touch any other bucket or secret.

```hcl
resource "aws_iam_instance_profile" "web" { role = aws_iam_role.web.name }
```
EC2 can't take a role directly. It takes an **instance profile**, which is a wrapper around the role.
The launch template points at this.

### `storage.tf`: the S3 bucket

```hcl
bucket = "${var.project}-uploads-${data.aws_caller_identity.current.account_id}"
```
Bucket names are globally unique across all of AWS, so the account ID is appended to avoid clashes.

| Block | What it does |
|---|---|
| `force_destroy = true` | `destroy` can delete the bucket even when it contains files |
| `public_access_block` | Four switches that make it impossible to make the bucket public, even by mistake |
| `ownership_controls` `BucketOwnerEnforced` | Turns off ACLs; access is controlled only by policies |
| `server_side_encryption` `AES256` | Every object is encrypted at rest |
| `uploads_tls_only` policy | `Deny` every `s3:*` action when `aws:SecureTransport` is `false`, so plain HTTP requests are rejected |

`depends_on` is the one place where the order is written by hand. The bucket policy must wait for the public
access block, and there's no reference between them for Terraform to infer that.

### `database.tf`: RDS MySQL

```hcl
resource "aws_db_subnet_group" "main" { subnet_ids = local.subnet_ids }
```
Tells RDS which subnets it may use. AWS requires at least two AZs here even for a single-AZ database.

Key lines in `aws_db_instance.main`:

| Line | Meaning |
|---|---|
| `engine = "mysql"`, `engine_version = "8.4"` | MySQL 8.4 |
| `instance_class = "db.t3.micro"` | Free-tier size |
| `allocated_storage = 20`, `storage_type = "gp3"` | 20 GB SSD |
| `storage_encrypted = true` | Disk encrypted at rest |
| `db_name = "appdb"`, `username = "appadmin"` | Database and admin user created at startup |
| `manage_master_user_password = true` | **No password in the code.** RDS generates one and stores it in Secrets Manager |
| `publicly_accessible = false` | No public IP; reachable only inside the VPC |
| `multi_az = false` | Single AZ (cost). `true` would add a standby copy in the other AZ |
| `backup_retention_period = 1` | Automatic daily backups kept 1 day |
| `skip_final_snapshot = true`, `deletion_protection = false` | Lets `destroy` delete it without leftovers (demo setting, not production) |

### `alb.tf`: load balancer

Three resources chained together:

1. **`aws_lb.web`**: the load balancer, internet-facing (`internal = false`), in both subnets, with the alb SG.
   `drop_invalid_header_fields` discards malformed HTTP headers (protection against request smuggling).
2. **`aws_lb_target_group.web`**: the pool of servers that receives traffic on port 8000.
   - `health_check`: GET `/health` every 15 s. It must return `200` within 5 s. 2 failures mark it unhealthy;
     2 successes mark it healthy again.
   - `deregistration_delay = 30`: when an instance is removed, it gets 30 s to finish in-flight requests
     (the default is 300).
3. **`aws_lb_listener.http`**: listens on port 80 and forwards everything to the target group.

The target group starts empty. The ASG registers instances into it (see `target_group_arns` below).

### `compute.tf`: launch template, Auto Scaling group, scaling policy

```hcl
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}
```
AWS publishes the latest Amazon Linux 2023 AMI ID in a public SSM parameter, so there's no hard-coded AMI ID
that goes stale.

**Launch template** (the recipe for each server):
- `image_id`, `instance_type`, `vpc_security_group_ids`, `iam_instance_profile`: the AMI, size, web SG and role.
- `metadata_options { http_tokens = "required" }` enforces IMDSv2. `http_put_response_hop_limit = 1` also
  stops containers on the host from reaching the metadata service.
- `credit_specification { cpu_credits = "standard" }`: t3 instances default to "unlimited", which bills
  extra when CPU stays high. Standard just throttles instead.
- `user_data = base64encode(templatefile(...))` is the boot script (next section). `templatefile` fills in
  the `${...}` placeholders. `app_hash` changes whenever any app file changes, which creates a new template
  version and rolls the new code out to the instances.
- `update_default_version = true`: every change makes a new template version and marks it default.

**Auto Scaling group:**

| Line | Meaning |
|---|---|
| `min_size 2`, `max_size 4`, `desired_capacity 2` | Never fewer than 2, never more than 4, start at 2 |
| `vpc_zone_identifier = local.subnet_ids` | Spread instances across both AZs |
| `target_group_arns` | Register every new instance with the ALB target group |
| `health_check_type = "ELB"` | Replace an instance when the **ALB** says it's unhealthy, not only when EC2 says it's dead |
| `health_check_grace_period = 420` | Ignore health for the first 7 minutes, because first boot spends ~3-5 minutes installing packages |
| `enabled_metrics` | Sends group size metrics to CloudWatch (used by the dashboard) |
| `launch_template { version = ...latest_version }` | Always use the newest template version |
| `instance_refresh { strategy = "Rolling", min_healthy_percentage = 50 }` | When the template changes, replace instances one at a time, keeping at least half serving |
| `lifecycle { ignore_changes = [desired_capacity] }` | After creation the scaling policy owns the instance count, so Terraform must not reset it to 2 |

**Scaling policy:**
```hcl
policy_type = "TargetTrackingScaling"
predefined_metric_type = "ASGAverageCPUUtilization"
target_value = 50
```
Works like a thermostat. AWS creates the CloudWatch alarms itself and adds or removes instances to keep the
average CPU across the group near 50%.

### `user_data.sh.tftpl`: what each server runs on first boot

Runs once as root when an instance starts. Line by line:

| Line(s) | What it does |
|---|---|
| `set -euxo pipefail` | Stop on any error (`-e`), on undefined variables (`-u`), on a failure anywhere in a pipe (`pipefail`); print each command to the log (`-x`) |
| `dnf install -y python3.11 python3.11-pip mariadb105` | Python, and the MySQL client used by `dbshell` |
| `aws s3 cp --recursive s3://.../app/ /opt/app/` | Downloads the app code Terraform uploaded to S3 |
| `curl ... global-bundle.pem` | Downloads Amazon's RDS certificate authority, so the app can verify it's really talking to RDS |
| `python3.11 -m venv` + `pip install -r requirements.txt` | Isolated environment with Flask, gunicorn, PyMySQL, boto3, Pillow |
| `useradd --system ... appuser` | A user with no login shell. The app runs as this user, not root |
| `cat > photoshield.service` | systemd unit: starts gunicorn (2 worker processes) on port 8000, passes settings as environment variables, restarts it if it crashes (`Restart=always`) |
| `NoNewPrivileges`, `PrivateTmp` | Hardening: the process can't gain root and gets its own `/tmp` |
| `cat > /usr/local/bin/dbshell` | Demo helper: fetches the secret, opens a `mariadb` shell to RDS with verified TLS |
| `systemctl enable --now statusapp` | Start the app now and on every reboot |

The `'EOF'` in quotes means the shell won't expand `$` inside the heredoc. Terraform's `${region}` and similar
were already replaced before the script ever reached the server.

### `monitoring.tf`: alarms and dashboard

- `aws_sns_topic.alerts`: a notification channel. Alarms publish to it; email subscribers get the message.
- `aws_sns_topic_subscription.email` has `count = var.alert_email == "" ? 0 : 1`. That's the Terraform idiom for
  an optional resource: 0 copies when no email is set.

| Alarm | Metric | Fires when |
|---|---|---|
| `unhealthy-hosts` | `UnHealthyHostCount` | ≥ 1 unhealthy target for 2 minutes in a row |
| `alb-5xx` | `HTTPCode_ELB_5XX_Count` | ≥ 10 errors from the ALB in 5 minutes |
| `db-cpu-high` | RDS `CPUUtilization` | > 80% for 10 minutes |
| `db-storage-low` | RDS `FreeStorageSpace` | < 2 GiB (2147483648 bytes) |

`treat_missing_data = "notBreaching"`: no data (no traffic) counts as fine, not as an alarm.

The **dashboard** is JSON built with `jsonencode`. It has six graphs: requests/errors, p95 latency,
healthy/unhealthy targets, ASG size, web CPU and RDS CPU/connections. In the metrics arrays, `"."` means
"same as the line above".

### `outputs.tf`

Prints the site URL, ASG name, DB endpoint, secret ARN, bucket name and dashboard link after `apply`.
Read any of them later with `terraform output app_url`.

## Part 3: `app/`, the PhotoShield app

Terraform uploads the whole `app/` folder to `s3://<bucket>/app/`, and each instance downloads it at boot
(`aws s3 cp` in the user-data script). That's needed because EC2 user data is limited to 16 KB.

**`photoshield.py`: image processing (no AWS code, so it's unit-tested locally)**

| Function | What it does |
|---|---|
| `analyse(img)` | Reads the EXIF data: GPS latitude/longitude (converted from degrees/minutes/seconds to decimal), device, lens, serial, time, software, plus XMP, comments and PNG text fields |
| `clean(img)` | Applies the EXIF rotation to the pixels, then rebuilds the image from raw pixel data (`Image.frombytes`) and saves a JPEG. Nothing but pixels survives |
| `process(data)` | Checks the format and refuses images over 40 MP **before** decoding (decompression bomb protection), then analyses and cleans |
| `sample_photo()` | Generates a 6 MP test photo tagged with a Pixel 8 and the TIET campus GPS position, so the demo never depends on a real phone photo |

**`app.py`: web layer**

| Part | What it does |
|---|---|
| `os.environ["DB_HOST"]` and friends | Settings come from the systemd `Environment=` lines, which come from Terraform. Nothing is hard-coded |
| `boto3.client(...)` | AWS SDK. **No keys passed**: boto3 finds the instance role's temporary credentials automatically |
| `imds(path)` | IMDSv2: `PUT` for a token, then `GET` the instance ID and AZ with it (shown in the header) |
| `db_credentials()` / `connect()` | Password from Secrets Manager (cached 300 s), MySQL over verified TLS, one retry on error 1045 if the password was rotated |
| `POST /scan`, `POST /sample` | Process the image, `put_object` the clean copy to `clean/<random>.jpg`, record the scan in RDS, render the result with two **pre-signed URLs** (preview and download, valid 10 minutes) |
| `GET /` | Upload form, totals and the 8 latest scans from the `scans` table |
| `GET /health` | Returns `ok`. Deliberately doesn't touch the DB |

Details worth knowing:
- The SQL uses `%s` placeholders, so values are never pasted into the SQL string (prevents SQL injection).
- The `scans` table stores only counts and yes/no flags, **never the coordinates**.
- If RDS is down, scanning still works; only the statistics are missing.
- Uploads are capped at 15 MB (`MAX_CONTENT_LENGTH`).

## Part 4: Practice (free, nothing gets created)

`validate` and `plan` only read from AWS. They never create anything and cost nothing.
Run these in PowerShell:

```
cd E:\CodexData\Workspaces\CloudHAProject\terraform
$env:TF_PLUGIN_CACHE_DIR = 'E:\tools\tf-plugin-cache'; $env:TMP = 'E:\tools\tmp'; $env:TEMP = 'E:\tools\tmp'
E:\tools\terraform\terraform.exe validate
E:\tools\terraform\terraform.exe plan
```

`plan` while the stack is down lists all 39 resources it would create, with every setting. Scroll through it
and find the RDS block, the security group rules and the ASG. It's the same code, shown as the values AWS will
receive.

Then, before running each exercise, **predict** what `plan` will show:

1. In `variables.tf`, change `asg_max` default from `4` to `6`. Run `plan`. (When the stack is up, this shows
   `~ update in-place` on the ASG. When it's down, check that the ASG block says `max_size = 6`.)
2. In `database.tf`, change `multi_az = false` to `true`. Find it in the plan.
3. Delete the `db_from_web` rule block in `network.tf`. Run `plan`. Count the resources. What would break
   on the site if you applied this? (Answer: the page would show "Database: unreachable", but `/health`
   would stay OK, so the servers wouldn't be replaced.)

**Undo every change afterwards.** If you have git by then, use `git checkout .`; otherwise, edit the values back.
Never run `apply` for these exercises.

## Part 5: Questions about the code itself

- *Where is the order of creation defined?* Nowhere explicitly. Terraform infers it from references,
  e.g. RDS refers to the db SG, so the SG is created first. The only manual one is `depends_on` in `storage.tf`.
- *How does the server know the DB address?* Terraform reads `aws_db_instance.main.address` after RDS is created,
  puts it into the user-data template, and the script writes it into the systemd unit as `DB_HOST`.
- *How does the app get the password?* `DB_SECRET_ARN` tells it which secret to read; the instance role allows
  `GetSecretValue` on that one secret; boto3 fetches it at runtime.
- *What is `for_each` vs `count`?* `for_each` makes one copy per item in a set or map (one subnet per AZ).
  `count` makes N copies; `count = 0` is how you make a resource optional.
- *What's the state file?* `terraform.tfstate`, Terraform's record of which real AWS IDs belong to which block.
  `plan` compares code vs state vs reality. It's git-ignored because it can contain sensitive values.
- *What happens if you change the app code?* `app.py` is embedded in user data, so the launch template gets a
  new version, and the ASG's instance refresh replaces instances one at a time with no downtime.
- *Why `templatefile` instead of writing the script inline?* It keeps the bash script a real file you can read,
  with variables filled in at build time.
