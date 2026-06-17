from shared.errors import AppError


class FileMissingError(AppError):
    message = "File not found."
    code = "file_not_found"


class FileUploadConsistencyError(AppError):
    """Raised when DB commit fails after S3 upload and S3 compensation also fails."""

    message = "File upload failed; S3 compensation also failed — manual cleanup needed."
    code = "file_upload_consistency_error"
