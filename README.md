# Azure DevOps PR Comment Analyzer

A Python CLI tool to extract and analyze meaningful Pull Request comments
linked to Azure DevOps work items, filtering out system and status noise.

## Features
- Fetch PR comments linked to work items
- Filters system / auto-generated comments
- Team-based classification (configurable)
- Excel report generation
- Pie & Bar charts for comment distribution
- Debug mode with filtering statistics
- Public-repo safe (no secrets)

## Setup
```bash
pip install -r requirements.txt
export AZURE_DEVOPS_PAT=your_pat_here
```

## Usage
```bash
python main.py --tickets 123456 123457 --debug
```

## Output
- pr_comment_report.xlsx
- comments_by_team_pie.png
- comments_by_team_bar.png
