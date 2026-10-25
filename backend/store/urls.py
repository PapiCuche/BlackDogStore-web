from rest_framework.routers import DefaultRouter
from . import supply_alerts
from . import count_views
from .inventory_ai_views import AdminInventoryAISettingsView
from .text_preview_views import (
    AdminTextPreviewMatchView, AdminTextPreviewOfferDraftFileView, AdminTextPreviewOfferDraftView,
)
from .availability_views import AdminAvailabilityView
from .checkout_quote_views import CheckoutQuoteView
from .staff_views import (
    AdminStaffInvitationDetailView, AdminStaffInvitationListView,
    AdminStaffListView, AdminStaffMembershipView, StaffInvitationAcceptView,
)
from .fiscal_views import (
    AdminFiscalDocumentCdrView, AdminFiscalDocumentCreditNoteView,
    AdminFiscalDocumentDebitNoteView, AdminFiscalDocumentPdfView,
    AdminFiscalDocumentReconcileView, AdminFiscalDocumentSubmitView,
    AdminFiscalDocumentXmlView, AdminOrderFiscalDocumentView,
)
from .fiscal_summary_views import (
    AdminFiscalSummaryDetailView, AdminFiscalSummaryListView,
    AdminFiscalSummaryStatusView, AdminFiscalSummarySubmitView,
)
from .fiscal_void_views import (
    AdminFiscalDocumentGrantView, AdminFiscalDocumentNotGrantedView,
    AdminFiscalDocumentVoidView, AdminFiscalVoidDetailView,
    AdminFiscalVoidStatusView, AdminFiscalVoidSubmitView,
)
from .views import (
    CategoryViewSet,
    ProductViewSet,
    OrderViewSet,
    CartViewSet,
    CreateCheckoutSessionView,
    IzipayNotificationView,
    MiCuentaWebNotificationView,
    PaymentStatusView,
    ReviewViewSet,
    CouponValidateView,
)
from .auth_views import (
    RegisterView, UserDetailView, LoginView, RefreshView, LogoutView, CsrfView,
    VerifyEmailView, VerifyEmailCodeView, ResendVerificationView,
    PasswordResetRequestView, PasswordResetConfirmView,
    ChangePasswordView,
)
from .admin_views import (
    AdminUserListView, AdminUserRoleView, AdminAuditLogListView,
    AdminProductListView, AdminProductDetailView, AdminProductInventoryAdjustView,
    AdminProductBulkVisibilityView,
    AdminCategoryDetailView,
    AdminCategoryListView,
    AdminOrderListView, AdminOrderDetailView, AdminOrderFulfillmentView, AdminOrderCancelView,
    AdminOrderRefundListView, AdminOrderRefundConfirmView, AdminOrderLeftMarkingCorrectView,
    AdminOrderReturnView, AdminOrderReturnReceiveView, AdminOrderReturnCloseView, AdminOrderReturnWithdrawView,
    AdminOrderUnitsView, AdminOrderUnitLookupView, AdminOrderUnitDetailView,
    AdminOrderReceiptPdfView, AdminOrderResendEmailView,
    AdminOrderReprocessStockExitView, AdminOrderRiskReviewView,
)
from .inventory_views import (
    AdminBestSellingView, AdminHighStockView, AdminInventorySummaryView,
    AdminLowStockView, AdminOrderSalesNotePdfView, AdminOrderSalesNoteView,
    AdminProductStockCardView, AdminStaleStockView, AdminStockMovementListView,
    # --- Phase 2D: multi-branch inventory ---
    AdminBranchStockListView, AdminBranchStockPolicyView,
    AdminInventoryBranchListView, AdminInventoryDashboardView,
    AdminReplenishmentView,
    AdminStockTransferCancelView, AdminStockTransferDetailView,
    AdminStockTransferDispatchView, AdminStockTransferItemsView,
    AdminStockTransferListView, AdminStockTransferReceiveView,
)
from .pos_views import (
    AdminCommissionSettingsView,
    AdminPosContextView,
    AdminPosPreviewView,
    AdminPosLookupView,
    AdminPosSaleView,
    AdminPosSearchView,
    AdminPosUnitLookupView,
    AdminProductBarcodeView,
    AdminProductCodeGenerateView,
)
from .sales_analytics_views import (
    AdminCommissionsView,
    AdminSalesDashboardView,
    AdminSalesMarginView,
    AdminSalesReplenishmentView,
)
from .import_views import (
    AdminImportErrorReportView,
    AdminImportHistoryView,
    AdminImportInspectView,
    AdminImportJobView,
    AdminInventoryExportView,
    AdminProductImagesImportApplyView,
    AdminProductImagesImportRulesView,
    AdminProductImportApplyView,
    AdminProductImportPreviewView,
    AdminProductTemplateView,
    AdminStockImportApplyView,
    AdminUnitImportApplyView,
    AdminUnitImportPreviewView,
    AdminUnitImportTemplateView,
    AdminStockImportPreviewView,
)
from .inventory_export_views import AdminInventoryExportsView
from .promotion_views import (
    AdminCouponView,
    AdminPosCombosView,
    AdminPromotionDetailView,
    AdminPromotionListView,
)
from .customer_views import (
    AdminCustomerDetailView,
    AdminCustomerListView,
)
from .dev_accounts_views import DevDemoAccountsView
from .storefront_media_views import AdminStorefrontImageUploadView, StorefrontImageView
from .product_media_views import (
    AdminProductImageDetailView, AdminProductImageListView, AdminProductImageOrderView,
)
from .storefront_content_views import (
    AdminStorefrontListContentDetailView,
    AdminStorefrontListContentView,
    AdminStorefrontCampaignActionView,
    AdminStorefrontCampaignDetailView,
    AdminStorefrontCampaignListView,
    AdminStorefrontCampaignPreviewView,
    AdminStorefrontPageView,
)
from .print_views import (
    AdminPrintAgentDetailView, AdminPrintAgentListView, AdminPrinterDetailView,
    AdminPrinterListView, AdminPrintJobListView, AdminPrintJobRetryView,
)
from .mail_preview_views import MailPreviewIndexView, MailPreviewView
from .measurement_views import MeasurementConfigView
from .label_settings_views import AdminLabelSettingsView
from .settings_views import (
    AdminCompanySettingsView, AdminSequenceDetailView, AdminSequenceListView,
    AdminSequenceScopeView, StorefrontConfigView,
)
from .tenant_views import (
    AdminBranchDetailView, AdminBranchListView, AdminCompanyDetailView,
    AdminCompanyFulfillmentBranchView,
    AdminCompanyListView, AdminMembershipDetailView, AdminMembershipListView,
    MyMembershipsView,
)
from .tracking_views import AccountRepairClaimView, AccountRepairsView
from .account_views import (
    AccountAddressDefaultView, AccountAddressDetailView, AccountAddressListView, AccountProfileView,
    AccountVerifyEmailConfirmView, AccountVerifyEmailSendView,
)
from .delivery_views import AdminCarrierDetailView, AdminCarrierListView, AdminOrderDeliveryView
from .google_views import GoogleConfigView, GoogleLinkView, GoogleSignInView
from .supplier_views import (
    AdminOfferDeactivateView, AdminOfferDetailView, AdminOfferExportView, AdminOfferHistoryView,
    AdminOfferImportApplyView, AdminOfferImportPreviewView, AdminOfferListView, AdminOfferTemplateView,
    AdminOrderSupplyActionView, AdminProductSaleModeView, AdminSupplierDetailView, AdminSupplierListView,
    AdminSupplierProductRefView, AdminSupplierSettlementView,
)
from .device_catalog_views import (
    AdminAttributeListView, AdminAttributeOptionsView, AdminCatalogSettingsView, AdminGradeListView,
    AdminGradeSeedView, AdminModelBulkVisibilityView, AdminModelDetailView, AdminModelListView,
    AdminOptionDetailView,
    AdminProductVariantView, PublicModelDetailView, PublicModelListView,
)
from .registration_views import AdminProductRegisterView
from .stock_unit_views import (
    StockUnitPhotoDetailView, StockUnitPhotoListView,
    ProductSerializationView, SerializedProductListView, StockUnitActionView,
    StockUnitDetailView, StockUnitListView,
)
from .integration_views import (
    IntegrationActionView, IntegrationDetailView, IntegrationDraftView, IntegrationListView,
)
from .access_views import (
    AdminAreaDetailView, AdminAreaListView, AdminRoleAssignmentDetailView,
    AdminRoleAssignmentListView, AdminRoleDetailView, AdminRoleListView,
    CapabilityCatalogView, InternalDashboardView, MyCompanyAccessView,
)
from django.urls import path, include
from .sales_note_control_views import (
    AdminSalesNoteDetailView, AdminSalesNoteListView, AdminSalesNoteRemarkView, AdminSalesNoteVoidView,
)

router = DefaultRouter()
router.register('categories', CategoryViewSet, basename='category')
router.register('products', ProductViewSet, basename='product')
router.register('orders', OrderViewSet, basename='order')
router.register('cart', CartViewSet, basename='cart')
router.register('reviews', ReviewViewSet, basename='review')

urlpatterns = [
    path('', include(router.urls)),
    path('auth/register/', RegisterView.as_view(), name='auth-register'),
    path('auth/login/', LoginView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', RefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    # INTEGRATIONS-CONSOLE — platform masters only. Secrets are write-only.
    path('admin/integrations/', IntegrationListView.as_view(), name='admin-integrations'),
    path('admin/integrations/<slug:provider_id>/', IntegrationDetailView.as_view(), name='admin-integration'),
    path('admin/integrations/<slug:provider_id>/draft/', IntegrationDraftView.as_view(), name='admin-integration-draft'),
    path('admin/integrations/<slug:provider_id>/<slug:action>/', IntegrationActionView.as_view(), name='admin-integration-action'),
    path('auth/csrf/', CsrfView.as_view(), name='auth-csrf'),
    # Sólo con DEBUG=True; con DEBUG=False responde 404. Ver el módulo.
    path(
        'dev/demo-accounts/',
        DevDemoAccountsView.as_view(), name='dev-demo-accounts',
    ),
    # Vista previa de los correos. Sólo con DEBUG=True; si no, 404. Ver el módulo.
    path('dev/mail-preview/', MailPreviewIndexView.as_view(), name='dev-mail-preview'),
    path('dev/mail-preview/<slug:example>/', MailPreviewView.as_view(), name='dev-mail-preview-example'),
    path('auth/google/config/', GoogleConfigView.as_view(), name='auth-google-config'),
    path('auth/google/', GoogleSignInView.as_view(), name='auth-google'),
    path('auth/google/link/', GoogleLinkView.as_view(), name='auth-google-link'),
    path('auth/me/', UserDetailView.as_view(), name='auth-me'),
    path('auth/verify-email/', VerifyEmailView.as_view(), name='auth-verify-email'),
    path('auth/verify-email/code/', VerifyEmailCodeView.as_view(), name='auth-verify-email-code'),
    path('auth/resend-verification/', ResendVerificationView.as_view(), name='auth-resend-verification'),
    path('auth/password-reset/request/', PasswordResetRequestView.as_view(), name='auth-password-reset-request'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='auth-password-reset-confirm'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='auth-change-password'),
    path('payments/create-checkout-session/', CreateCheckoutSessionView.as_view(), name='create-checkout-session'),
    # The gateway's notification endpoint. Named for the role, not the
    # provider's product name, so the route survives the next migration.
    path(
        'payments/izipay/notification/',
        IzipayNotificationView.as_view(),
        name='izipay-notification',
    ),
    # Izipay's other product. Only ONE of the two answers: the one named by
    # PAYMENT_PROVIDER. The other is a 404.
    path(
        'payments/micuentaweb/notification/',
        MiCuentaWebNotificationView.as_view(),
        name='micuentaweb-notification',
    ),
    path('payments/status/', PaymentStatusView.as_view(), name='payment-status'),
    path('coupons/validate/', CouponValidateView.as_view(), name='coupon-validate'),
    # C2.1: el desglose tributario ANTES de pagar. No crea ni cobra nada.
    path('checkout/quote/', CheckoutQuoteView.as_view(), name='checkout-quote'),
    path('admin/users/', AdminUserListView.as_view(), name='admin-users'),
    path('admin/users/<int:pk>/role/', AdminUserRoleView.as_view(), name='admin-user-role'),
    path('admin/audit-logs/', AdminAuditLogListView.as_view(), name='admin-audit-logs'),
    path('admin/products/', AdminProductListView.as_view(), name='admin-products'),
    path('admin/products/bulk-visibility/', AdminProductBulkVisibilityView.as_view(), name='admin-product-bulk-visibility'),
    # COMMERCE-ADMIN-UX-01 — «Registrar artículo»: one article, the existing services, one transaction.
    path('admin/products/register/', AdminProductRegisterView.as_view(), name='admin-product-register'),
    path('admin/products/<int:pk>/', AdminProductDetailView.as_view(), name='admin-product-detail'),
    path('admin/products/<int:pk>/images/', AdminProductImageListView.as_view(), name='admin-product-images'),
    # DEVICE-COMMERCE-01 · DC2 — models, options, variants, grades.
    path('admin/products/<int:pk>/variant/', AdminProductVariantView.as_view(), name='admin-product-variant'),
    # DC3 — suppliers, offers and the supply of on-order lines.
    path('admin/suppliers/', AdminSupplierListView.as_view(), name='admin-suppliers'),
    path('admin/suppliers/settlement/', AdminSupplierSettlementView.as_view(), name='admin-supplier-settlement'),
    path('admin/suppliers/<int:pk>/', AdminSupplierDetailView.as_view(), name='admin-supplier'),
    path('admin/suppliers/<int:pk>/product-refs/', AdminSupplierProductRefView.as_view(),
         name='admin-supplier-product-refs'),
    path('admin/supplier-offers/', AdminOfferListView.as_view(), name='admin-supplier-offers'),
    path('admin/supplier-offers/import/template/', AdminOfferTemplateView.as_view(),
         name='admin-supplier-offers-template'),
    path('admin/inventory/text-preview/match/', AdminTextPreviewMatchView.as_view(),
         name='admin-inventory-text-preview-match'),
    path('admin/inventory/text-preview/offer-draft/', AdminTextPreviewOfferDraftView.as_view(),
         name='admin-inventory-text-preview-offer-draft'),
    path('admin/inventory/text-preview/offer-draft/file/', AdminTextPreviewOfferDraftFileView.as_view(),
         name='admin-inventory-text-preview-offer-draft-file'),
    path('admin/inventory/ai-settings/', AdminInventoryAISettingsView.as_view(), name='admin-inventory-ai-settings'),
    path('admin/supplier-offers/import/preview/', AdminOfferImportPreviewView.as_view(),
         name='admin-supplier-offers-preview'),
    path('admin/supplier-offers/import/<int:pk>/apply/', AdminOfferImportApplyView.as_view(),
         name='admin-supplier-offers-apply'),
    path('admin/supplier-offers/export/', AdminOfferExportView.as_view(), name='admin-supplier-offers-export'),
    path('admin/supplier-offers/<int:pk>/', AdminOfferDetailView.as_view(), name='admin-supplier-offer'),
    path('admin/supplier-offers/<int:pk>/deactivate/', AdminOfferDeactivateView.as_view(),
         name='admin-supplier-offer-deactivate'),
    path('admin/supplier-offers/<int:pk>/history/', AdminOfferHistoryView.as_view(),
         name='admin-supplier-offer-history'),
    path('admin/products/<int:pk>/sale-mode/', AdminProductSaleModeView.as_view(), name='admin-product-sale-mode'),
    path('admin/orders/<int:pk>/supply/<int:line_pk>/<str:action>/', AdminOrderSupplyActionView.as_view(),
         name='admin-order-supply-action'),
    # SUPPLIER-DEVICE-FLOW-02 — «Alertas operativas» of the dashboard.
    path('admin/orders/alerts/', supply_alerts.AdminOrderAlertsView.as_view(), name='admin-order-alerts'),
    path('admin/catalog/models/', AdminModelListView.as_view(), name='admin-catalog-models'),
    path('admin/catalog/models/bulk-visibility/', AdminModelBulkVisibilityView.as_view(),
         name='admin-catalog-models-bulk-visibility'),
    path('admin/catalog/models/<int:pk>/', AdminModelDetailView.as_view(), name='admin-catalog-model'),
    path('admin/catalog/attributes/', AdminAttributeListView.as_view(), name='admin-catalog-attributes'),
    path('admin/catalog/attributes/<int:pk>/options/', AdminAttributeOptionsView.as_view(),
         name='admin-catalog-attribute-options'),
    path('admin/catalog/options/<int:pk>/', AdminOptionDetailView.as_view(), name='admin-catalog-option'),
    path('admin/catalog/grades/', AdminGradeListView.as_view(), name='admin-catalog-grades'),
    path('admin/catalog/grades/seed/', AdminGradeSeedView.as_view(), name='admin-catalog-grades-seed'),
    path('admin/catalog/settings/', AdminCatalogSettingsView.as_view(), name='admin-catalog-settings'),
    path('catalog/models/', PublicModelListView.as_view(), name='catalog-models'),
    path('catalog/models/<slug:slug>/', PublicModelDetailView.as_view(), name='catalog-model'),
    path('admin/products/<int:pk>/images/order/', AdminProductImageOrderView.as_view(), name='admin-product-images-order'),
    path('admin/products/<int:pk>/images/<int:image_id>/', AdminProductImageDetailView.as_view(), name='admin-product-image'),
    path('admin/products/<int:pk>/inventory-adjust/', AdminProductInventoryAdjustView.as_view(), name='admin-product-inventory-adjust'),
    path('admin/categories/', AdminCategoryListView.as_view(), name='admin-categories'),
    path('admin/categories/<int:pk>/', AdminCategoryDetailView.as_view(), name='admin-category'),
    path('admin/orders/', AdminOrderListView.as_view(), name='admin-orders'),
    path('admin/orders/<int:pk>/', AdminOrderDetailView.as_view(), name='admin-order-detail'),
    path('admin/orders/<int:pk>/fulfillment-status/', AdminOrderFulfillmentView.as_view(), name='admin-order-fulfillment'),
    path('admin/orders/<int:pk>/cancel/', AdminOrderCancelView.as_view(), name='admin-order-cancel'),
    # POST-DELIVERY-RETURN, phase 1: request (and read), receive, close, withdraw.
    path('admin/orders/<int:pk>/return/', AdminOrderReturnView.as_view(), name='admin-order-return'),
    path('admin/orders/<int:pk>/return/receive/', AdminOrderReturnReceiveView.as_view(),
         name='admin-order-return-receive'),
    path('admin/orders/<int:pk>/return/close/', AdminOrderReturnCloseView.as_view(), name='admin-order-return-close'),
    path('admin/orders/<int:pk>/return/withdraw/', AdminOrderReturnWithdrawView.as_view(),
         name='admin-order-return-withdraw'),
    path('admin/orders/<int:pk>/left-markings/<int:audit_id>/correct/', AdminOrderLeftMarkingCorrectView.as_view(),
         name='admin-order-left-marking-correct'),
    path('admin/orders/<int:pk>/refunds/', AdminOrderRefundListView.as_view(), name='admin-order-refunds'),
    path('admin/orders/<int:pk>/refunds/<int:refund_pk>/confirm/', AdminOrderRefundConfirmView.as_view(),
         name='admin-order-refund-confirm'),
    # SERIAL-PICK-01: the devices of a web order, identified when it is handed over.
    path('admin/orders/<int:pk>/units/', AdminOrderUnitsView.as_view(), name='admin-order-units'),
    path('admin/orders/<int:pk>/units/lookup/', AdminOrderUnitLookupView.as_view(), name='admin-order-unit-lookup'),
    path('admin/orders/<int:pk>/units/<int:unit_pk>/', AdminOrderUnitDetailView.as_view(), name='admin-order-unit-detail'),
    path('admin/orders/<int:pk>/reprocess-stock-exit/', AdminOrderReprocessStockExitView.as_view(), name='admin-order-reprocess-stock-exit'),
    # DELIVERY-01 — the delivery record of an order: carrier, agency, freight, waybill.
    path('admin/orders/<int:pk>/delivery/', AdminOrderDeliveryView.as_view(), name='admin-order-delivery'),
    # CLIENT-SEC-01 — a person decides the review an order was waiting for.
    path('admin/orders/<int:pk>/risk-review/', AdminOrderRiskReviewView.as_view(), name='admin-order-risk-review'),
    path('admin/orders/<int:pk>/receipt-pdf/', AdminOrderReceiptPdfView.as_view(), name='admin-order-receipt-pdf'),
    path('admin/orders/<int:pk>/resend-confirmation-email/', AdminOrderResendEmailView.as_view(), name='admin-order-resend-email'),

    # --- Phase 6.0: inventory (Kardex) + internal sales notes ---
    path('admin/inventory/summary/', AdminInventorySummaryView.as_view(), name='admin-inventory-summary'),
    path('admin/inventory/movements/', AdminStockMovementListView.as_view(), name='admin-inventory-movements'),
    path('admin/inventory/low-stock/', AdminLowStockView.as_view(), name='admin-inventory-low-stock'),
    path('admin/inventory/high-stock/', AdminHighStockView.as_view(), name='admin-inventory-high-stock'),
    path('admin/inventory/best-selling/', AdminBestSellingView.as_view(), name='admin-inventory-best-selling'),
    path('admin/inventory/no-movement/', AdminStaleStockView.as_view(), name='admin-inventory-no-movement'),
    path('admin/products/<int:pk>/stock-card/', AdminProductStockCardView.as_view(), name='admin-product-stock-card'),
    path('admin/orders/<int:pk>/sales-note/', AdminOrderSalesNoteView.as_view(), name='admin-order-sales-note'),
    # --- C2.2A.1: comprobantes electrónicos. NO es la nota de venta interna ---
    path('admin/orders/<int:pk>/fiscal-document/', AdminOrderFiscalDocumentView.as_view(), name='admin-order-fiscal-document'),
    path('admin/fiscal-documents/<int:pk>/credit-notes/', AdminFiscalDocumentCreditNoteView.as_view(), name='admin-fiscal-document-credit-note'),
    path('admin/fiscal-documents/<int:pk>/debit-notes/', AdminFiscalDocumentDebitNoteView.as_view(), name='admin-fiscal-document-debit-note'),
    path('admin/fiscal-documents/<int:pk>/grant/', AdminFiscalDocumentGrantView.as_view(), name='admin-fiscal-document-grant'),
    path('admin/fiscal-documents/<int:pk>/not-granted/', AdminFiscalDocumentNotGrantedView.as_view(), name='admin-fiscal-document-not-granted'),
    path('admin/fiscal-documents/<int:pk>/void/', AdminFiscalDocumentVoidView.as_view(), name='admin-fiscal-document-void'),
    path('admin/fiscal-void-communications/<int:pk>/', AdminFiscalVoidDetailView.as_view(), name='admin-fiscal-void-detail'),
    path('admin/fiscal-void-communications/<int:pk>/submit/', AdminFiscalVoidSubmitView.as_view(), name='admin-fiscal-void-submit'),
    path('admin/fiscal-void-communications/<int:pk>/status/', AdminFiscalVoidStatusView.as_view(), name='admin-fiscal-void-status'),
    path('admin/fiscal-documents/<int:pk>/submit/', AdminFiscalDocumentSubmitView.as_view(), name='admin-fiscal-document-submit'),
    path('admin/fiscal-documents/<int:pk>/reconcile/', AdminFiscalDocumentReconcileView.as_view(), name='admin-fiscal-document-reconcile'),
    path('admin/fiscal-summaries/', AdminFiscalSummaryListView.as_view(), name='admin-fiscal-summaries'),
    path('admin/fiscal-summaries/<int:pk>/', AdminFiscalSummaryDetailView.as_view(), name='admin-fiscal-summary-detail'),
    path('admin/fiscal-summaries/<int:pk>/submit/', AdminFiscalSummarySubmitView.as_view(), name='admin-fiscal-summary-submit'),
    path('admin/fiscal-summaries/<int:pk>/status/', AdminFiscalSummaryStatusView.as_view(), name='admin-fiscal-summary-status'),
    path('admin/fiscal-documents/<int:pk>/xml/', AdminFiscalDocumentXmlView.as_view(), name='admin-fiscal-document-xml'),
    path('admin/fiscal-documents/<int:pk>/cdr/', AdminFiscalDocumentCdrView.as_view(), name='admin-fiscal-document-cdr'),
    path('admin/fiscal-documents/<int:pk>/pdf/', AdminFiscalDocumentPdfView.as_view(), name='admin-fiscal-document-pdf'),
    path('admin/orders/<int:pk>/sales-note/pdf/', AdminOrderSalesNotePdfView.as_view(), name='admin-order-sales-note-pdf'),
    # SALES-NOTE-CONTROL-01 — issued notes: list, detail, the master's void, internal remarks.
    path('admin/sales-notes/', AdminSalesNoteListView.as_view(), name='admin-sales-notes'),
    path('admin/sales-notes/<int:pk>/', AdminSalesNoteDetailView.as_view(), name='admin-sales-note-detail'),
    path('admin/sales-notes/<int:pk>/void/', AdminSalesNoteVoidView.as_view(), name='admin-sales-note-void'),
    path('admin/sales-notes/<int:pk>/remarks/', AdminSalesNoteRemarkView.as_view(), name='admin-sales-note-remarks'),

    # --- Phase 2D: multi-branch inventory ---
    # The Phase 6.0 routes above keep their paths and their names; they gained a
    # `?branch=` parameter and lost nothing, so existing clients keep working.
    path('admin/inventory/branches/', AdminInventoryBranchListView.as_view(), name='admin-inventory-branches'),
    path('admin/inventory/dashboard/', AdminInventoryDashboardView.as_view(), name='admin-inventory-dashboard'),
    path('admin/inventory/stock/', AdminBranchStockListView.as_view(), name='admin-inventory-stock'),
    path('admin/inventory/stock/<int:pk>/policy/', AdminBranchStockPolicyView.as_view(), name='admin-inventory-stock-policy'),
    path('admin/inventory/replenishment/', AdminReplenishmentView.as_view(), name='admin-inventory-replenishment'),
    path('admin/inventory/transfers/', AdminStockTransferListView.as_view(), name='admin-inventory-transfers'),
    path('admin/inventory/transfers/<int:pk>/', AdminStockTransferDetailView.as_view(), name='admin-inventory-transfer-detail'),
    path('admin/inventory/transfers/<int:pk>/items/', AdminStockTransferItemsView.as_view(), name='admin-inventory-transfer-items'),
    path('admin/inventory/transfers/<int:pk>/dispatch/', AdminStockTransferDispatchView.as_view(), name='admin-inventory-transfer-dispatch'),
    path('admin/inventory/transfers/<int:pk>/receive/', AdminStockTransferReceiveView.as_view(), name='admin-inventory-transfer-receive'),
    path('admin/inventory/transfers/<int:pk>/cancel/', AdminStockTransferCancelView.as_view(), name='admin-inventory-transfer-cancel'),
    # Physical counts — store/count_views.py (Phase 2D, MIGRATION-C).
    path('admin/inventory/counts/', count_views.AdminInventoryCountListView.as_view(), name='admin-inventory-counts'),
    path('admin/inventory/counts/<int:pk>/', count_views.AdminInventoryCountDetailView.as_view(), name='admin-inventory-count-detail'),
    path('admin/inventory/counts/<int:pk>/products/', count_views.AdminInventoryCountProductsView.as_view(), name='admin-inventory-count-products'),
    path('admin/inventory/counts/<int:pk>/items/', count_views.AdminInventoryCountItemsView.as_view(), name='admin-inventory-count-items'),
    path('admin/inventory/counts/<int:pk>/scan/', count_views.AdminInventoryCountScanView.as_view(), name='admin-inventory-count-scan'),
    path('admin/inventory/counts/<int:pk>/recount/', count_views.AdminInventoryCountRecountView.as_view(), name='admin-inventory-count-recount'),
    path('admin/inventory/counts/<int:pk>/exclude/', count_views.AdminInventoryCountExcludeView.as_view(), name='admin-inventory-count-exclude'),
    path('admin/inventory/counts/<int:pk>/submit/', count_views.AdminInventoryCountSubmitView.as_view(), name='admin-inventory-count-submit'),
    path('admin/inventory/counts/<int:pk>/approve/', count_views.AdminInventoryCountApproveView.as_view(), name='admin-inventory-count-approve'),
    path('admin/inventory/counts/<int:pk>/cancel/', count_views.AdminInventoryCountCancelView.as_view(), name='admin-inventory-count-cancel'),

    # --- SaaS Phase 1: multi-tenant foundation ---
    path('admin/companies/', AdminCompanyListView.as_view(), name='admin-companies'),
    path('admin/companies/<int:pk>/', AdminCompanyDetailView.as_view(), name='admin-company-detail'),
    path('admin/companies/<int:pk>/fulfillment-branch/', AdminCompanyFulfillmentBranchView.as_view(), name='admin-company-fulfillment-branch'),
    path('admin/branches/', AdminBranchListView.as_view(), name='admin-branches'),
    path('admin/branches/<int:pk>/', AdminBranchDetailView.as_view(), name='admin-branch-detail'),
    path('admin/memberships/', AdminMembershipListView.as_view(), name='admin-memberships'),
    path('admin/memberships/<int:pk>/', AdminMembershipDetailView.as_view(), name='admin-membership-detail'),
    path('me/memberships/', MyMembershipsView.as_view(), name='me-memberships'),

    # --- SaaS Phase 2A.1: configurable areas, roles and assignments ---
    path('admin/capabilities/', CapabilityCatalogView.as_view(), name='admin-capabilities'),
    path('admin/areas/', AdminAreaListView.as_view(), name='admin-areas'),
    path('admin/areas/<int:pk>/', AdminAreaDetailView.as_view(), name='admin-area-detail'),
    path('admin/roles/', AdminRoleListView.as_view(), name='admin-roles'),
    path('admin/roles/<int:pk>/', AdminRoleDetailView.as_view(), name='admin-role-detail'),
    # --- H4.1: alta de personal por invitación ---
    path('admin/staff/', AdminStaffListView.as_view(), name='admin-staff'),
    path('admin/staff/<int:pk>/', AdminStaffMembershipView.as_view(), name='admin-staff-detail'),
    path('admin/staff/invitations/', AdminStaffInvitationListView.as_view(), name='admin-staff-invitations'),
    path('admin/staff/invitations/<int:pk>/<str:action>/', AdminStaffInvitationDetailView.as_view(), name='admin-staff-invitation-action'),
    # Pública para leer, autenticada para aceptar. Fuera de `admin/` a propósito:
    # quien acepta todavía no pertenece a la empresa.
    path('staff/invitations/accept/', StaffInvitationAcceptView.as_view(), name='staff-invitation-accept'),
    path('admin/membership-role-assignments/', AdminRoleAssignmentListView.as_view(), name='admin-role-assignments'),
    path('admin/membership-role-assignments/<int:pk>/', AdminRoleAssignmentDetailView.as_view(), name='admin-role-assignment-detail'),
    path('me/company-access/', MyCompanyAccessView.as_view(), name='me-company-access'),
    path('me/internal-dashboard/', InternalDashboardView.as_view(), name='me-internal-dashboard'),

    # --- SaaS Phase 3: company configuration and branding ---
    path('storefront/config/', StorefrontConfigView.as_view(), name='storefront-config'),
    # ANALYTICS-MARKETING: the public IDs of the measurement providers a master activated.
    path('measurement/config/', MeasurementConfigView.as_view(), name='measurement-config'),
    path(
        'storefront/images/<str:public_id>/',
        StorefrontImageView.as_view(), name='storefront-image',
    ),
    path(
        'admin/storefront/images/',
        AdminStorefrontImageUploadView.as_view(), name='admin-storefront-images',
    ),
    path('admin/company-settings/', AdminCompanySettingsView.as_view(), name='admin-company-settings'),
    # Impresión en la tienda: impresoras, agentes y cola, por sucursal.
    path('admin/printing/printers/', AdminPrinterListView.as_view(), name='admin-printers'),
    path('admin/printing/printers/<int:pk>/', AdminPrinterDetailView.as_view(), name='admin-printer'),
    path('admin/printing/agents/', AdminPrintAgentListView.as_view(), name='admin-print-agents'),
    path('admin/printing/agents/<int:pk>/', AdminPrintAgentDetailView.as_view(), name='admin-print-agent'),
    path('admin/printing/jobs/', AdminPrintJobListView.as_view(), name='admin-print-jobs'),
    path('admin/printing/jobs/<int:pk>/retry/', AdminPrintJobRetryView.as_view(), name='admin-print-job-retry'),
    # M12F — contenido comercial del escaparate.
    path(
        'admin/storefront/page/',
        AdminStorefrontPageView.as_view(), name='admin-storefront-page',
    ),
    path(
        'admin/storefront/campaigns/',
        AdminStorefrontCampaignListView.as_view(), name='admin-storefront-campaigns',
    ),
    path(
        'admin/storefront/campaigns/<int:pk>/',
        AdminStorefrontCampaignDetailView.as_view(),
        name='admin-storefront-campaign',
    ),
    path(
        'admin/storefront/campaigns/<int:pk>/preview/',
        AdminStorefrontCampaignPreviewView.as_view(),
        name='admin-storefront-campaign-preview',
    ),
    path(
        'admin/storefront/<str:kind>/',
        AdminStorefrontListContentView.as_view(),
        name='admin-storefront-list-content',
    ),
    path(
        'admin/storefront/<str:kind>/<int:pk>/',
        AdminStorefrontListContentDetailView.as_view(),
        name='admin-storefront-list-content-detail',
    ),
    path(
        'admin/storefront/campaigns/<int:pk>/<str:action>/',
        AdminStorefrontCampaignActionView.as_view(),
        name='admin-storefront-campaign-action',
    ),

    # --- Commercial Phase C1: point of sale + analytics ---
    # All internal control. `sales.pos.use` for the till, `sales.analytics.view`
    # for the numbers, and `inventory.reports` on top for the forecast detail.
    path('admin/pos/context/', AdminPosContextView.as_view(), name='admin-pos-context'),
    # COMMERCE-ADMIN-UX-01 — «Consultar disponibilidad»: read only, `sales.pos.use`.
    path('admin/availability/', AdminAvailabilityView.as_view(), name='admin-availability'),
    path('admin/pos/products/lookup/', AdminPosLookupView.as_view(), name='admin-pos-lookup'),
    path('admin/pos/products/search/', AdminPosSearchView.as_view(), name='admin-pos-search'),
    path('admin/pos/units/lookup/', AdminPosUnitLookupView.as_view(), name='admin-pos-unit-lookup'),
    path('admin/pos/sales/', AdminPosSaleView.as_view(), name='admin-pos-sale'),
    path('admin/products/<int:pk>/barcodes/', AdminProductBarcodeView.as_view(), name='admin-product-barcodes'),
    # LABEL-01 — a generated code only if the product has none (`products.manage`).
    path('admin/products/<int:pk>/barcodes/generate/', AdminProductCodeGenerateView.as_view(),
         name='admin-product-code-generate'),
    path('admin/pos/preview/', AdminPosPreviewView.as_view(), name='admin-pos-preview'),
    path('admin/sales/dashboard/', AdminSalesDashboardView.as_view(), name='admin-sales-dashboard'),
    # DC1 — margen comercial: `sales.analytics.view` + `costs.view`.
    path('admin/sales/margin/', AdminSalesMarginView.as_view(), name='admin-sales-margin'),
    path('admin/sales/commissions/', AdminCommissionsView.as_view(), name='admin-sales-commissions'),
    # --- Commercial Phase C1.3: promotions, combos and coupon administration ---
    path('admin/sales/promotions/', AdminPromotionListView.as_view(), name='admin-promotions'),
    path('admin/sales/promotions/<int:pk>/', AdminPromotionDetailView.as_view(), name='admin-promotion-detail'),
    path('admin/sales/coupons/', AdminCouponView.as_view(), name='admin-coupons'),
    path('admin/sales/coupons/<int:pk>/', AdminCouponView.as_view(), name='admin-coupon-detail'),
    path('admin/pos/combos/', AdminPosCombosView.as_view(), name='admin-pos-combos'),

    # --- C1.4: bulk imports ------------------------------------------------
    path('admin/imports/inspect/', AdminImportInspectView.as_view(), name='admin-import-inspect'),
    path('admin/imports/', AdminImportHistoryView.as_view(), name='admin-import-history'),
    path('admin/imports/<int:pk>/', AdminImportJobView.as_view(), name='admin-import-job'),
    path('admin/imports/<int:pk>/errors.csv', AdminImportErrorReportView.as_view(), name='admin-import-errors'),
    # The same report at the address Caddy and the Next proxy actually send:
    # both add a trailing slash to every /api/ path (IMPORT-ERRORS-404).
    path('admin/imports/<int:pk>/errors.csv/', AdminImportErrorReportView.as_view(), name='admin-import-errors-slash'),
    path('admin/products/import/preview/', AdminProductImportPreviewView.as_view(), name='admin-product-import-preview'),
    path('admin/products/import/<int:pk>/apply/', AdminProductImportApplyView.as_view(), name='admin-product-import-apply'),
    path('admin/products/import/template/', AdminProductTemplateView.as_view(), name='admin-product-template'),
    # INVENTORY-UNIFIED-01: pictures in bulk. The preview is `products/import/preview/` with
    # `kind=images` (the one route sized for it); these two carry no file.
    path('admin/products/images/import/rules/', AdminProductImagesImportRulesView.as_view(), name='admin-product-images-import-rules'),
    path('admin/products/images/import/<int:pk>/apply/', AdminProductImagesImportApplyView.as_view(), name='admin-product-images-import-apply'),
    # --- SERIALIZED-STOCK: Inventario › Equipos ---
    # `products/` before `<int:pk>/`, so the literal is not read as an id.
    path('admin/inventory/units/', StockUnitListView.as_view(), name='admin-inventory-units'),
    # LABEL-02 — prefix of device codes and the device label profile (`company.manage` to change).
    path('admin/label-settings/', AdminLabelSettingsView.as_view(), name='admin-label-settings'),
    path('admin/inventory/units/products/', SerializedProductListView.as_view(), name='admin-inventory-unit-products'),
    path('admin/inventory/units/products/<int:pk>/serialization/', ProductSerializationView.as_view(), name='admin-inventory-unit-product-serialization'),
    # UNIT-IMPORT: the device upload («Equipos»). One row, one device.
    path('admin/inventory/units/import/template/', AdminUnitImportTemplateView.as_view(), name='admin-unit-import-template'),
    path('admin/inventory/units/import/preview/', AdminUnitImportPreviewView.as_view(), name='admin-unit-import-preview'),
    path('admin/inventory/units/import/<int:pk>/apply/', AdminUnitImportApplyView.as_view(), name='admin-unit-import-apply'),
    path('admin/inventory/units/<int:pk>/', StockUnitDetailView.as_view(), name='admin-inventory-unit-detail'),
    path('admin/inventory/units/<int:pk>/write-off/', StockUnitActionView.as_view(action='write-off'), name='admin-inventory-unit-write-off'),
    path('admin/inventory/units/<int:pk>/reserve/', StockUnitActionView.as_view(action='reserve'), name='admin-inventory-unit-reserve'),
    path('admin/inventory/units/<int:pk>/release/', StockUnitActionView.as_view(action='release'), name='admin-inventory-unit-release'),
    path('admin/inventory/units/<int:pk>/return/', StockUnitActionView.as_view(action='return'), name='admin-inventory-unit-return'),
    path('admin/inventory/units/<int:pk>/photos/', StockUnitPhotoListView.as_view(), name='admin-inventory-unit-photos'),
    path('admin/inventory/units/<int:pk>/photos/<int:photo_id>/', StockUnitPhotoDetailView.as_view(),
         name='admin-inventory-unit-photo'),
    path('admin/inventory/import/preview/', AdminStockImportPreviewView.as_view(), name='admin-stock-import-preview'),
    path('admin/inventory/import/<int:pk>/apply/', AdminStockImportApplyView.as_view(), name='admin-stock-import-apply'),
    path('admin/inventory/export/', AdminInventoryExportView.as_view(), name='admin-inventory-export'),
    # INVENTORY-UNIFIED-01: the catalogue, the quantities, the devices and all three in a ZIP.
    path('admin/inventory/exports/<str:kind>/', AdminInventoryExportsView.as_view(), name='admin-inventory-exports'),
    path('admin/sales/commission-settings/', AdminCommissionSettingsView.as_view(), name='admin-commission-settings'),
    path('admin/sales/commission-settings/<int:pk>/', AdminCommissionSettingsView.as_view(), name='admin-commission-setting-detail'),
    path('admin/sales/replenishment/', AdminSalesReplenishmentView.as_view(), name='admin-sales-replenishment'),

    # --- SaaS Phase 4: customers (internal CRM) ---
    # No public counterpart. A customer's document, phone, address and the notes
    # about them are internal control only.
    path('admin/customers/', AdminCustomerListView.as_view(), name='admin-customers'),
    path('admin/customers/<int:pk>/', AdminCustomerDetailView.as_view(), name='admin-customer-detail'),

    # --- TRACKING: the signed-in customer's own repairs ---
    path('account/repairs/', AccountRepairsView.as_view(), name='account-repairs'),
    path('account/repairs/claim/', AccountRepairClaimView.as_view(), name='account-repairs-claim'),
    # --- DELIVERY-01: a company's carriers ---
    path('admin/carriers/', AdminCarrierListView.as_view(), name='admin-carriers'),
    path('admin/carriers/<int:pk>/', AdminCarrierDetailView.as_view(), name='admin-carrier-detail'),
    # --- CLIENT-SEC-01: the signed-in buyer's own profile in this shop ---
    path('account/profile/', AccountProfileView.as_view(), name='account-profile'),
    # --- CLIENT-SEC-01 · F7: a signed-in account proves its own mailbox ---
    path('account/verify-email/send/', AccountVerifyEmailSendView.as_view(), name='account-verify-email-send'),
    path('account/verify-email/confirm/', AccountVerifyEmailConfirmView.as_view(), name='account-verify-email-confirm'),
    # --- DELIVERY-01: the signed-in buyer's own address book in this shop ---
    path('account/addresses/', AccountAddressListView.as_view(), name='account-addresses'),
    path('account/addresses/<int:pk>/', AccountAddressDetailView.as_view(), name='account-address-detail'),
    path('account/addresses/<int:pk>/default/', AccountAddressDefaultView.as_view(), name='account-address-default'),

    # --- SaaS Phase 2E: internal document sequences ---
    # `scope/` before `<int:pk>/` so the literal segment is not swallowed by
    # the numeric one.
    path('admin/sequences/', AdminSequenceListView.as_view(), name='admin-sequences'),
    path('admin/sequences/scope/', AdminSequenceScopeView.as_view(), name='admin-sequence-scope'),
    path('admin/sequences/<int:pk>/', AdminSequenceDetailView.as_view(), name='admin-sequence-detail'),
]
