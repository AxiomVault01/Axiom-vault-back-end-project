# =====================
# SEND OTP
# =====================

@extend_schema(
    request=SendOTPSerializer,
    tags=["Auth"]
)
@action(
    detail=False,
    methods=["post"],
    url_path="send-otp"
)
def send_otp(self, request):

    serializer = SendOTPSerializer(
        data=request.data
    )

    serializer.is_valid(
        raise_exception=True
    )

    OTPService.generate(
        serializer.validated_data["email"]
    )

    return Response(
        {
            "message": "OTP sent successfully"
        },
        status=status.HTTP_200_OK
    )


# =====================
# VERIFY OTP
# =====================

@extend_schema(
    request=VerifyOTPSerializer,
    tags=["Auth"]
)
@action(
    detail=False,
    methods=["post"],
    url_path="verify-otp"
)
def verify_otp(self, request):

    serializer = VerifyOTPSerializer(
        data=request.data
    )

    serializer.is_valid(
        raise_exception=True
    )

    valid, error = OTPService.verify(
        serializer.validated_data["email"],
        serializer.validated_data["code"]
    )

    if not valid:
        return Response(
            {"error": error},
            status=status.HTTP_400_BAD_REQUEST
        )

    return Response(
        {
            "message": "OTP verified"
        },
        status=status.HTTP_200_OK
    )


# =====================
# RESEND OTP
# =====================

@extend_schema(
    request=ResendOTPSerializer,
    tags=["Auth"]
)
@action(
    detail=False,
    methods=["post"],
    url_path="resend-otp"
)
def resend_otp(self, request):

    serializer = ResendOTPSerializer(
        data=request.data
    )

    serializer.is_valid(
        raise_exception=True
    )

    OTPService.generate(
        serializer.validated_data["email"]
    )

    return Response(
        {
            "message": "OTP resent successfully"
        },
        status=status.HTTP_200_OK
    )