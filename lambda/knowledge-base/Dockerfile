FROM public.ecr.aws/lambda/python:3.12

# Install git and AWS CLI (Amazon Linux 2023 uses dnf/microdnf)
RUN microdnf install -y git && \
    pip install awscli && \
    microdnf clean all

# Copy function code
COPY docs_uploader.py ${LAMBDA_TASK_ROOT}/

# Set the CMD to your handler
CMD ["docs_uploader.lambda_handler"]
