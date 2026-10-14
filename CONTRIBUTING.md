# Contributing to TROVE

Thank you for your interest in contributing to TROVE! We welcome and encourage contributions
of many kinds. Our goal is to keep this a positive, inclusive, successful, and growing community.

## Table of Contents

- [Policy on AI Use](#policy-on-ai-use)
- [Getting Started](#getting-started)
- [Setting Up Your Development Environment](#setting-up-your-development-environment)
- [Making Changes](#making-changes)
- [Submitting Changes](#submitting-changes)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Enhancements](#suggesting-enhancements)
- [Coding Guidelines](#coding-guidelines)
- [Testing](#testing)
- [Documentation](#documentation)
- [Getting Help](#getting-help)

## Policy on AI Use
We welcome the use of AI assisted development. However, we ask that developers follow a set of guidelines 
to ensure the safe and documented use of AI. This is primarily motivated by the AAS Journal guidelines on
AI use, which require the disclosure of models used when submitting a journal. The guidlines are as follows:

1. *All AI use should be closely monitored*. AI makes mistakes, especially when it does not understand the 
   full context of the project. If you are using a chatbot, carefully read it's suggestion before copy and
   pasting it into the codebase. If using an agent, ensure your `AGENT.md` file encourages frequent questions
   and careful implementation.
2. *All AI models used should be included as authors on commits when possible*. This is important for us to track the 
   contributions of AI vs. humans in the code, including in the commit history and git blame. See
   [PR 327 on TROVE](https://github.com/astro-trove/trove/pull/327) for an example. If an AI was used in
   chatbot mode and cannot commit directly to the repo, it should be explicitly mentioned in the commit 
   which AI model was used using the following format (which will insert the co-author statement in  a new paragraph): 
   `git commit -m "implement feature xyz" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`.

## Getting Started

### Prerequisites

- GitHub account
- Git installed on your machine
- Python

### Set Up GitHub and Git

If you're new to Git and GitHub, see the [GitHub documentation for contributing to projects](https://docs.github.com/en/get-started/quickstart/contributing-to-projects).

### Getting the Code Locally

### Option 1 (for the general public): Create a Fork and Clone

1. Fork the repository by clicking the "Fork" button on GitHub
2. Clone your fork locally:

```
git clone https://github.com/YOUR-USERNAME/convert_flux.git
cd convert_flux
```

### Option 2 (for TROVE collaborators): Create a branch and clone the repo directly
1. If the issue you want to fix is not yet open, open it!
2. Create a branch from an existing issue by clicking the "Create a branch" link on the right column of the issue
3. Then you can run
```
git clone https://github.com/astro-trove/trove
cd trove
git switch <your-new-branch-name>
```

## Setting Up Your Development Environment

### Create an Isolated Python Environment (Recommended)

Using conda (Suggested approach, replace env-name):

```
conda create -n env-name python=3.12
conda activate env-name
```


Or using venv (replace env-name, make sure you have python 3.11 or 3.12 installed):

```
python3.12 -m venv env-name
source env-name/bin/activate
```

## Making Changes

### Create a Branch

Always create a new branch for your changes:

```
git switch main
git pull upstream main --ff-only
git switch -c descriptive-branch-name
```

Use descriptive branch names like `fix-bug-123` or `add-feature-xyz`.

### Make Your Changes

1. Make code changes - Use your editor or IDE to make modifications
2. Test your changes and add unit tests - See Testing section
3. Update documentation - If applicable

### Commit Your Changes
Commit message guidelines:
- Limit the first line to 72 characters.
- Reference issues and pull requests liberally after the first line. See the github documentation on [closing keywords](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/using-keywords-in-issues-and-pull-requests). You can also just reference issues, without closing them, by using a # followed by the issue number (i.e., `#11`).

```
git add files-you-changed
git commit -m "Clear, descriptive commit message"
```

## Submitting Changes

### Push Your Branch

```
git push
```


### Create a Pull Request

1. Go to your fork (or the TROVE repo) on GitHub
2. Click "Compare & pull request"
3. Fill out the PR template completely
4. If your PR is a work in progress, but you still want feedback, create a draft PR instead
5. Submit the PR

Tips for a successful PR:
- Reference any related issues
- Keep PRs focused on a single feature or bug fix
- Ensure all tests pass
- Keep commit history clean
- Respond promptly to feedback

### Pull Request Template

The below is a loose guideline for a PR template. It does not need to be followed exactly, and feel free to omit
content if it is irrelevant.

```
## Summary 
Describe in a few sentences what feature you want to merge in. If it pertains to a specific issue, tag it here like this: Issue #11 and GitHub will link and close the issue upon merge 

## Details 
Help guide your reviewers through your changes. Keep it short - “how” things are done should be apparent from you code, and “why” they are done this way should be in the code as comments. Don’t repeat yourself! 
## Testing 
Document your testing here. Ideally, automation will run tests and the results will be available. If testing is more manual, paste here. If other, weird tests were needed, describe them. 
## Follow-on 
If other things came up and a new feature is needed for follow-on work, describe the unfinished business here. 
```

### Updating Your PR

If reviewers request changes:
```
git add files-you-changed
git commit -m "Address feedback: some detailed commit message"
git push
```

## Reporting Bugs

### Before Submitting a Bug Report

- Check the issue tracker to see if the bug has already been reported
- Check the documentation to ensure you're using the feature correctly

### Submit a Bug Report as Follows

Bug Issue Template

```
**Describe the bug**

A clear and concise description of what the bug is.

**To Reproduce**

Steps to reproduce the behavior:

1. Go to '...'
2. Click on '....'
3. Scroll down to '....'
4. See error

**Expected behavior**

A clear and concise description of what you expected to happen.

**Screenshots**

If applicable, add screenshots to help explain your problem.

**Environment info (please complete the following information):**

- PC hardware: [e.g. vagrant VM, flight computer hostname, personal computer]
- OS: [e.g. Debian, Ubuntu, Mac OS]
- Software version [e.g. release number or commit hash]

**Additional context**

Add any other context about the problem here.
```

## Suggesting Enhancements

### Before Suggesting an Enhancement

- Check the issue tracker to see if it has already been suggested
- Check the documentation and code to see if the feature already exists

### Submit an Enhancement Suggestion as Follows

Enhancement Request Issue Template

```
## Task
Describe in a few sentences what the task is. We need to [...] in order
to [...].
## Acceptance
Describe what must exist in order for the task to be considered done:
code changes, specific/nonstandard testing expectations, documentation,
or a follow-on task.
## Details
All the nitty-gritty details someone might need to know in order to get
started. Any helpful context or links, code snippets, line numbers, file
names.
```

## Coding Guidelines

### Style Guide

This project follows PEP 8 with the following guidelines:

- Maximum line length: 88 characters (Black default)
- Use type hints where possible
- Write docstrings for all public functions and classes

### Code Formatting

We use ruff for code formatting. If you've installed pre-commit, this will run automatically.

To manually format your code run
```
ruff
```


## Testing

### Running Tests

Run the test suite with:
```
pytest
```


Run tests for a specific module:
```
pytest tests/module-name.py
```


### Writing Tests

- Place tests in the `tests/` directory
- Use descriptive test names
- Aim for good coverage of your new code
- See existing tests for examples

## Documentation

### Building Documentation
```
cd docs
make html
```


View the built documentation in `docs/_build/html/index.html`

### Writing Documentation

- Write clear docstrings in the [NumPy style](https://numpydoc.readthedocs.io/en/latest/format.html#docstring-standard)
- Update relevant documentation files
- Include examples where appropriate

## Getting Help

Questions? Reach out!

## Additional Resources

- [Project Documentation](https://datatrove.as.arizona.edu/docs)
- [Issue Tracker](https://github.com/astro-trove/trove/issues)

---

Thank you for contributing to TROVE! 🎉
