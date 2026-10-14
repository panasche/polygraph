output "vpc_id" {
  description = "VPC id."
  value       = aws_vpc.this.id
}

output "vpc_cidr_block" {
  description = "VPC CIDR."
  value       = aws_vpc.this.cidr_block
}

output "azs" {
  description = "Availability zones in use."
  value       = local.azs
}

output "public_subnet_ids" {
  description = "Public subnets (ALB, NAT)."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnets (ECS tasks)."
  value       = aws_subnet.private[*].id
}

output "data_subnet_ids" {
  description = "Isolated subnets (RDS)."
  value       = aws_subnet.data[*].id
}

output "data_subnet_cidrs" {
  description = "CIDRs of the data subnets."
  value       = aws_subnet.data[*].cidr_block
}

output "nat_public_ips" {
  description = "Public IPs the tasks use for outbound calls (e.g. to GitHub)."
  value       = aws_eip.nat[*].public_ip
}
